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

def find_pagination_info(driver, base_url):
    """Find pagination information using various methods"""
    pagination_info = {
        'next_links': [],
        'total_pages': None,
        'current_page': None
    }
    
    # Check sitemap
    try:
        sitemap_url = urljoin(base_url, '/sitemap.xml')
        response = requests.get(sitemap_url, timeout=5)
        if response.ok:
            soup = BeautifulSoup(response.text, 'xml')
            urls = soup.find_all('url')
            page_pattern = re.compile(r'page/\d+|page=\d+')
            pagination_info['next_links'].extend([
                url.find('loc').text for url in urls 
                if url.find('loc') and page_pattern.search(url.find('loc').text)
            ])
    except:
        pass
    
    # Check DOM for pagination elements
    try:
        pagination_elements = driver.find_elements(
            By.CSS_SELECTOR, 
            '.pagination, .nav-links, .pager, [class*="pagination"], [class*="paging"]'
        )
        for element in pagination_elements:
            numbers = re.findall(r'\d+', element.text)
            if numbers:
                pagination_info['total_pages'] = max(map(int, numbers))
                try:
                    current = element.find_element(
                        By.CSS_SELECTOR, 
                        '.current, .active, [aria-current="page"]'
                    )
                    if current:
                        pagination_info['current_page'] = int(re.search(r'\d+', current.text).group())
                except:
                    pass
    except:
        pass
        
    return pagination_info
    
def gather_page_content(driver, base_url, progress_bar):
    """Gather content using multiple strategies"""
    links = set()
    
    log_progress(progress_bar, "Analyzing network requests...")
    content_endpoints = analyze_browser_logs(driver)
    for endpoint in content_endpoints:
        try:
            response = requests.get(endpoint, timeout=5)
            if response.ok:
                try:
                    data = response.json()
                    json_str = json.dumps(data)
                    url_pattern = rf'{base_url}[^"\']*'
                    found_urls = re.findall(url_pattern, json_str)
                    links.update(found_urls)
                except:
                    pass
        except:
            continue
    
    log_progress(progress_bar, "Checking for article elements...")
    selectors = [
        "article a", "div.post a", ".blog-post a",
        "h2 a", "h3 a", ".title a",
        "[class*='article'] a", "[class*='post'] a",
        ".card-title", ".entry-title a"
    ]
    
    for selector in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                try:
                    href = element.get_attribute('href')
                    if href and is_valid_url(href) and href.startswith(base_url):
                        links.add(href)
                except:
                    continue
        except:
            continue
    
    log_progress(progress_bar, "Looking for schema.org markup...")
    try:
        schema_scripts = driver.find_elements(
            By.CSS_SELECTOR,
            'script[type="application/ld+json"]'
        )
        for script in schema_scripts:
            try:
                data = json.loads(script.get_attribute('innerHTML'))
                if isinstance(data, dict):
                    if data.get('@type') in ['Article', 'BlogPosting', 'NewsArticle']:
                        url = data.get('url')
                        if url and is_valid_url(url) and url.startswith(base_url):
                            links.add(url)
            except:
                continue
    except:
        pass
    
    return list(links)

def load_more_content(driver, base_url, progress_bar):
    """Load content using multiple strategies"""
    all_links = set()
    page_unchanged_count = 0
    max_unchanged = 3
    
    log_progress(progress_bar, f"Starting content discovery on {base_url}")
    
    while page_unchanged_count < max_unchanged:
        current_links = set(gather_page_content(driver, base_url, progress_bar))
        new_links = current_links - all_links
        
        if not new_links:
            page_unchanged_count += 1
            log_progress(progress_bar, f"No new content found (attempt {page_unchanged_count}/{max_unchanged})")
        else:
            page_unchanged_count = 0
            all_links.update(new_links)
            log_progress(progress_bar, f"Found {len(new_links)} new links (total: {len(all_links)})")
        
        # Try pagination
        pagination = find_pagination_info(driver, base_url)
        if pagination['next_links']:
            log_progress(progress_bar, f"Found pagination with {len(pagination['next_links'])} additional pages")
            for link in pagination['next_links'][:5]:
                try:
                    log_progress(progress_bar, f"Navigating to page: {link}")
                    driver.get(link)
                    time.sleep(2)
                    new_page_links = gather_page_content(driver, base_url, progress_bar)
                    all_links.update(new_page_links)
                except Exception as e:
                    log_progress(progress_bar, f"Error accessing pagination: {str(e)}")
                    continue
            break
        
        # Try infinite scroll
        log_progress(progress_bar, "Attempting infinite scroll...")
        last_height = driver.execute_script("return document.body.scrollHeight")
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)
        
        # Try load more buttons
        log_progress(progress_bar, "Looking for 'Load More' buttons...")
        load_more_selectors = [
            ".load-more", "#load-more", "[class*='load-more']",
            "button:contains('Load More')", "a:contains('Load More')",
            "[class*='infinite-scroll']", ".next", ".more",
            ".js-load-more", ".infinite-loader", ".pagination__next"
        ]
        
        button_found = False
        for selector in load_more_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for element in elements:
                    if element.is_displayed():
                        log_progress(progress_bar, f"Found and clicking '{selector}' button")
                        driver.execute_script("arguments[0].click();", element)
                        time.sleep(3)
                        button_found = True
            except:
                continue
        
        if not button_found:
            log_progress(progress_bar, "No load more buttons found")
            
        # Check if scrolling made a difference
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height and not button_found:
            page_unchanged_count += 1
    
    log_progress(progress_bar, f"Content discovery complete. Found {len(all_links)} total links")
    return list(all_links)

def extract_content(driver, base_url, exclude_types):
    """Extract content using multiple strategies"""
    content = []
    
    content_selectors = [
        "article", ".post", ".content", 
        "[class*='article']", "[class*='post']",
        "main", "#main", ".main"
    ]
    
    for selector in content_selectors:
        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
            )
            break
        except:
            continue
    
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    
    schema_elements = soup.find_all('script', type='application/ld+json')
    for element in schema_elements:
        try:
            data = json.loads(element.string)
            if isinstance(data, dict):
                if 'articleBody' in data:
                    content.append(f"[ARTICLE] {data['articleBody']}\n")
                if 'description' in data:
                    content.append(f"[DESCRIPTION] {data['description']}\n")
        except:
            continue
    
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
                    content.append(f"[EXTERNAL LINK] {href}\n")
                elif href.startswith('/'):
                    content.append(f"[INTERNAL LINK] {urljoin(base_url, href)}\n")
    
    return content

def scrape_single_page(url, base_url, exclude_types):
    """Scrape a single page"""
    options = create_chrome_options()
    driver = webdriver.Chrome(service=Service(), options=options)
    try:
        driver.get(url)
        time.sleep(2)
        return extract_content(driver, base_url, exclude_types)
    finally:
        driver.quit()

def scrape_pages(base_url, initial_url, max_depth, exclude_types, max_urls, target_date, progress_bar):
    """Main scraping function"""
    visited = set()
    all_content = []
    
    log_progress(progress_bar, f"Initializing scraper for {base_url}")
    
    options = create_chrome_options()
    driver = webdriver.Chrome(service=Service(), options=options)
    try:
        log_progress(progress_bar, "Loading initial page...")
        driver.get(initial_url)
        time.sleep(2)
        log_progress(progress_bar, "Beginning content discovery...")
        all_links = load_more_content(driver, base_url, progress_bar)
        log_progress(progress_bar, f"Found {len(all_links)} links to process")
    except Exception as e:
        log_progress(progress_bar, f"Error during initial page load: {str(e)}")
        driver.quit()
        return []
    finally:
        driver.quit()

    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_url = {}
        for url in all_links:
            if max_urls is None or len(visited) < max_urls:
                if url not in visited and not is_unwanted_link(url, base_url):
                    visited.add(url)
                    future_to_url[executor.submit(scrape_single_page, url, base_url, exclude_types)] = url

        log_progress(progress_bar, f"Processing {len(future_to_url)} pages...")
        completed = 0
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                content = future.result()
                completed += 1
                log_progress(progress_bar, f"Processed ({completed}/{len(future_to_url)}): {url}")
                if content:
                    st.session_state.scraped_urls.append(url)
                    all_content.extend([f"\n[URL] {url}\n"])
                    all_content.extend(content)
                else:
                    log_progress(progress_bar, f"No content extracted from: {url}")
            except Exception as e:
                log_progress(progress_bar, f"Error scraping {url}: {str(e)}")

    log_progress(progress_bar, f"Scraping complete. Processed {len(visited)} pages.")
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