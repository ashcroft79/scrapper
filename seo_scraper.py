import streamlit as st
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from bs4 import BeautifulSoup
import requests
from urllib.parse import urljoin, urlparse
import re
import json
import hashlib
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

def create_chrome_options():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    return options

def is_valid_url(url):
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False

def clean_text(text):
    return re.sub(r'\s+', ' ', text).strip()

def should_exclude(element):
    exclude_classes = ['nav', 'menu', 'footer', 'sidebar', 'advertisement', 'cookie', 'popup']
    exclude_ids = ['nav', 'menu', 'footer', 'sidebar', 'ad']
    
    cookie_keywords = [
        'cookie', 'gdpr', 'privacy', 'tracking', 'analytics', 'consent',
        'session', 'storage', 'duration', 'browser', 'local storage',
        'pixel tracker', 'http cookie'
    ]
    
    text = element.get_text().lower()
    if any(keyword in text for keyword in cookie_keywords):
        return True

    for parent in element.parents:
        if parent.has_attr('class'):
            if any(cls in parent.get('class', []) for cls in exclude_classes):
                return True
        if parent.has_attr('id'):
            if any(id in parent.get('id', '') for id in exclude_ids):
                return True
    
    return False

def is_blog_post(text):
    blog_keywords = ['blog', 'post', 'article', 'news']
    return any(keyword in text.lower() for keyword in blog_keywords)

def is_after_date(text, target_date):
    date_pattern = r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\b'
    match = re.search(date_pattern, text)
    if match:
        date_str = match.group(0)
        date = datetime.strptime(date_str, '%b %d, %Y')
        return date >= target_date
    return True

def is_unwanted_link(url, base_url):
    unwanted_patterns = [
        '/cookie-policy', '/privacy-policy', '/terms-and-conditions',
        '/about-us', '/contact', '/careers', '/sitemap'
    ]
    is_external = not url.startswith(base_url)
    return any(pattern in url.lower() for pattern in unwanted_patterns) or is_external

def log_progress(progress_bar, message):
    """Append new message to progress display"""
    if 'messages' not in st.session_state:
        st.session_state.messages = []
    st.session_state.messages.append(f"{datetime.now().strftime('%H:%M:%S')} - {message}")
    progress_bar.markdown("\n".join(st.session_state.messages))

def analyze_browser_logs(driver):
    """Analyze browser logs for XHR requests"""
    logs = driver.get_log('performance')
    content_endpoints = []
    
    for entry in logs:
        try:
            log = json.loads(entry['message'])['message']
            if (
                'Network.requestWillBeSent' in log['method'] and
                'request' in log['params'] and
                'url' in log['params']['request']
            ):
                url = log['params']['request']['url']
                if any(pattern in url.lower() for pattern in [
                    '/api/', '/content/', '/posts/', '/articles/',
                    'page=', 'offset=', 'limit=', 'load-more'
                ]):
                    content_endpoints.append(url)
        except:
            continue
            
    return content_endpoints

def classify_url(url):
    """Classify URL by content type"""
    url_lower = url.lower()
    
    # Document links
    if any(ext in url_lower for ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']):
        return 'document'
        
    # Image links
    if any(ext in url_lower for ext in ['.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp']):
        return 'image'
        
    # Blog/Article patterns
    if any(pattern in url_lower for pattern in [
        '/blog/', '/article/', '/post/', '/news/',
        '/resources/', '/insights/', '/knowledge/'
    ]):
        return 'article'
        
    # Standard pages
    return 'page'

def find_all_links(soup, base_url):
    """Find all valid links on page"""
    links = set()
    
    # Standard links
    for a in soup.find_all('a', href=True):
        href = a.get('href')
        full_url = urljoin(base_url, href)
        if is_valid_url(full_url):
            links.add(full_url)
    
    # Look for links in onclick events
    onclick_elements = soup.find_all(attrs={"onclick": True})
    for element in onclick_elements:
        onclick = element.get('onclick')
        urls = re.findall(r'(?:href=|window\.location=|redirect\()[\'"](.*?)[\'"]', onclick)
        for url in urls:
            full_url = urljoin(base_url, url)
            if is_valid_url(full_url):
                links.add(full_url)
    
    # Look for links in data attributes
    data_elements = soup.find_all(attrs=lambda x: any(k.startswith('data-') for k in x.keys()))
    for element in data_elements:
        for attr in element.attrs:
            if attr.startswith('data-'):
                value = element[attr]
                if isinstance(value, str) and (value.startswith('http') or value.startswith('/')):
                    full_url = urljoin(base_url, value)
                    if is_valid_url(full_url):
                        links.add(full_url)
    
    return links

def find_embedded_content(soup, content_map, base_url):
    """Find embedded content like images and documents"""
    
    # Images
    for img in soup.find_all('img', src=True):
        src = img.get('src')
        if src:
            full_url = urljoin(base_url, src)
            if is_valid_url(full_url):
                content_map['image'].add(full_url)
    
    # Document links
    for a in soup.find_all('a', href=True):
        href = a.get('href')
        if href:
            full_url = urljoin(base_url, href)
            if any(ext in full_url.lower() for ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']):
                content_map['document'].add(full_url)

def handle_dynamic_content(driver, base_url, api_endpoints, progress_bar):
    """Handle dynamically loaded content"""
    dynamic_content = {
        'article': set(),
        'page': set()
    }
    
    for endpoint in api_endpoints:
        try:
            response = requests.get(endpoint, timeout=5)
            if response.ok:
                data = response.json()
                json_str = json.dumps(data)
                url_pattern = rf'{base_url}[^"\']*'
                found_urls = re.findall(url_pattern, json_str)
                
                for url in found_urls:
                    if is_valid_url(url):
                        content_type = classify_url(url)
                        if content_type in ['article', 'page']:
                            dynamic_content[content_type].add(url)
        except:
            continue
    
    # Try infinite scroll
    last_height = driver.execute_script("return document.body.scrollHeight")
    scroll_attempts = 0
    max_attempts = 5
    
    while scroll_attempts < max_attempts:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)
        
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            scroll_attempts += 1
        else:
            scroll_attempts = 0
            last_height = new_height
            
            # Get new content after scroll
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            links = find_all_links(soup, base_url)
            
            for link in links:
                if link.startswith(base_url):
                    content_type = classify_url(link)
                    if content_type in ['article', 'page']:
                        dynamic_content[content_type].add(link)
    
    return dynamic_content
    
def discover_site_content(driver, base_url, progress_bar):
    """Comprehensive site content discovery"""
    content_map = {
        'page': set(),
        'article': set(),
        'document': set(),
        'image': set(),
        'api_endpoints': set()
    }
    
    visited = set()
    to_visit = {base_url}
    
    log_progress(progress_bar, "Starting comprehensive site discovery...")
    
    while to_visit:
        current_url = to_visit.pop()
        if current_url in visited:
            continue
            
        visited.add(current_url)
        log_progress(progress_bar, f"Exploring: {current_url}")
        
        try:
            driver.get(current_url)
            time.sleep(2)
            
            # Check for dynamic content loading
            api_endpoints = analyze_browser_logs(driver)
            content_map['api_endpoints'].update(api_endpoints)
            
            if api_endpoints:
                log_progress(progress_bar, f"Found {len(api_endpoints)} API endpoints")
                dynamic_results = handle_dynamic_content(driver, base_url, api_endpoints, progress_bar)
                for content_type, urls in dynamic_results.items():
                    content_map[content_type].update(urls)
            
            # Get all links from current page
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            links = find_all_links(soup, base_url)
            
            # Classify and store links
            for link in links:
                if link.startswith(base_url) and link not in visited:
                    content_type = classify_url(link)
                    content_map[content_type].add(link)
                    if content_type in ['page', 'article']:
                        to_visit.add(link)
            
            # Look for embedded content
            find_embedded_content(soup, content_map, base_url)
            
            log_progress(progress_bar, f"Found {sum(len(v) for v in content_map.values())} total items")
            
        except Exception as e:
            log_progress(progress_bar, f"Error processing {current_url}: {str(e)}")
            continue
    
    return content_map

def extract_content(driver, url, base_url, exclude_types):
    """Extract content from a single page"""
    content = []
    
    try:
        driver.get(url)
        time.sleep(2)
        
        content_type = classify_url(url)
        log_prefix = f"[{content_type.upper()}]"
        content.append(f"{log_prefix} URL: {url}\n")
        
        # Handle different content types
        if content_type == 'document':
            content.append(f"{log_prefix} Document Link: {url}\n")
            return content
            
        if content_type == 'image':
            content.append(f"{log_prefix} Image Link: {url}\n")
            return content
        
        # For articles and pages, extract text content
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Try to get structured data first
        schema_elements = soup.find_all('script', type='application/ld+json')
        for element in schema_elements:
            try:
                data = json.loads(element.string)
                if isinstance(data, dict):
                    if 'articleBody' in data:
                        content.append(f"[ARTICLE_BODY] {data['articleBody']}\n")
                    if 'description' in data:
                        content.append(f"[DESCRIPTION] {data['description']}\n")
            except:
                continue
        
        # Extract main content
        for element in soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'a']):
            if should_exclude(element):
                continue

            if element.name in ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li']:
                if 'text' not in exclude_types:
                    text = clean_text(element.get_text())
                    if text and len(text) > 20:
                        content.append(f"[{element.name.upper()}] {text}\n")
            elif element.name == 'a' and 'links' not in exclude_types:
                href = element.get('href')
                if href:
                    if href.startswith(('http', 'https')):
                        content.append(f"[EXTERNAL_LINK] {href}\n")
                    elif href.startswith('/'):
                        content.append(f"[INTERNAL_LINK] {urljoin(base_url, href)}\n")
        
        return content
        
    except Exception as e:
        content.append(f"[ERROR] Failed to extract content: {str(e)}\n")
        return content

def scrape_pages(base_url, initial_url, max_depth, exclude_types, max_urls, target_date, progress_bar):
    """Main scraping function"""
    all_content = []
    
    log_progress(progress_bar, f"Initializing scraper for {base_url}")
    
    options = create_chrome_options()
    driver = webdriver.Chrome(service=Service(), options=options)
    
    try:
        # Discover all site content first
        content_map = discover_site_content(driver, base_url, progress_bar)
        
        total_urls = sum(len(urls) for content_type, urls in content_map.items())
        log_progress(progress_bar, f"Content discovery complete. Found {total_urls} items to process")
        
        # Process discovered content
        processed_urls = set()
        urls_to_process = []
        
        # Prioritize articles and pages
        urls_to_process.extend(content_map['article'])
        urls_to_process.extend(content_map['page'])
        urls_to_process.extend(content_map['document'])
        urls_to_process.extend(content_map['image'])
        
        if max_urls:
            urls_to_process = urls_to_process[:max_urls]
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_url = {}
            for url in urls_to_process:
                if url not in processed_urls and not is_unwanted_link(url, base_url):
                    processed_urls.add(url)
                    future_to_url[executor.submit(extract_content, driver, url, base_url, exclude_types)] = url

            completed = 0
            total = len(future_to_url)
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    content = future.result()
                    completed += 1
                    log_progress(progress_bar, f"Processed ({completed}/{total}): {url}")
                    if content:
                        st.session_state.scraped_urls.append(url)
                        all_content.extend(content)
                except Exception as e:
                    log_progress(progress_bar, f"Error scraping {url}: {str(e)}")

    except Exception as e:
        log_progress(progress_bar, f"Error during scraping: {str(e)}")
        return []
        
    finally:
        driver.quit()
    
    log_progress(progress_bar, "Scraping complete")
    return all_content

def main():
    st.title("Advanced Web Scraper for Competitor Analysis")

    # Clear previous messages when starting new scrape
    if 'messages' in st.session_state:
        del st.session_state.messages

    url = st.text_input("Enter the website URL to scrape:")
    max_depth = st.number_input("Enter the maximum depth to scrape:", min_value=0, max_value=5, value=1, step=1)
    max_urls = st.number_input("Maximum number of URLs to scrape (leave blank for no limit):", min_value=1, value=None)
    date_filter = st.date_input("Only include content published after (leave blank for no filter):", value=None)
    
    exclude_types = st.multiselect(
        "Select content types to exclude:",
        ['text', 'links', 'images', 'blog posts'],
        default=[]
    )
    
    if st.button("Scrape"):
        if not is_valid_url(url):
            st.error("Please enter a valid URL.")
            return
        
        st.info("Scraping in progress...")
        progress_container = st.empty()
        st.session_state.scraped_urls = []
        
        try:
            target_date = datetime.combine(date_filter, datetime.min.time()) if date_filter else None
            content = scrape_pages(url, url, max_depth, exclude_types, max_urls, target_date, progress_container)
            
            if content:
                filename = f"{urlparse(url).netloc}_analysis.txt"
                with open(filename, "w", encoding="utf-8") as f:
                    for line in content:
                        if 'blog posts' in exclude_types and is_blog_post(line):
                            continue
                        f.write(line)
                
                st.success(f"Analysis completed! Content saved to {filename}")
                
                with open(filename, "r", encoding="utf-8") as f:
                    file_content = f.read()
                
                st.download_button(
                    label="Download Content",
                    data=file_content,
                    file_name=filename,
                    mime="text/plain"
                )
                
                st.subheader("Preview of Extracted Content")
                st.text_area("Content Preview", value="".join(content[:20]), height=300)
                
                st.subheader("Scraped URLs")
                for url in st.session_state.scraped_urls:
                    st.write(url)
            else:
                st.warning("No content could be extracted. Please check the URL and try again.")
            
        except Exception as e:
            st.error(f"Error during scraping: {str(e)}")

if __name__ == "__main__":
    main()