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
import hashlib
import json
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

def create_chrome_options():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    return options

def is_valid_url(url):
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False

def clean_text(text):
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()

def should_exclude(element):
    exclude_classes = ['nav', 'menu', 'footer', 'sidebar', 'advertisement', 'cookie', 'popup', 'header']
    exclude_ids = ['nav', 'menu', 'footer', 'sidebar', 'ad', 'header']
    
    cookie_keywords = [
        'cookie', 'gdpr', 'privacy', 'tracking', 'analytics', 'consent',
        'session', 'storage', 'duration', 'browser', 'local storage',
        'pixel tracker', 'http cookie'
    ]
    
    try:
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
    except:
        return False
    
    return False

def is_blog_post(text):
    blog_keywords = ['blog', 'post', 'article', 'news', 'story']
    return any(keyword in text.lower() for keyword in blog_keywords)

def is_after_date(text, target_date):
    if not target_date:
        return True
        
    date_patterns = [
        r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\b',
        r'\b\d{4}-\d{2}-\d{2}\b',
        r'\b\d{2}/\d{2}/\d{4}\b'
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                date_str = match.group(0)
                if '-' in date_str:
                    date = datetime.strptime(date_str, '%Y-%m-%d')
                elif '/' in date_str:
                    date = datetime.strptime(date_str, '%m/%d/%Y')
                else:
                    date = datetime.strptime(date_str, '%b %d, %Y')
                return date >= target_date
            except:
                continue
    return True

def is_unwanted_link(url, base_url):
    unwanted_patterns = [
        '/cookie-policy', '/privacy-policy', '/terms-and-conditions',
        '/about-us', '/contact', '/careers', '/sitemap', '/login',
        '/register', '/signup', '/cart', '/checkout', '/account'
    ]
    
    try:
        is_external = not url.startswith(base_url)
        is_unwanted = any(pattern in url.lower() for pattern in unwanted_patterns)
        has_fragment = '#' in url
        return is_external or is_unwanted or has_fragment
    except:
        return True

def log_progress(progress_bar, message):
    """Append new message to progress display"""
    try:
        if 'messages' not in st.session_state:
            st.session_state.messages = []
        st.session_state.messages.append(f"{datetime.now().strftime('%H:%M:%S')} - {message}")
        progress_bar.markdown("\n".join(st.session_state.messages))
    except:
        pass

def classify_url(url):
    """Classify URL by content type"""
    try:
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
            '/resources/', '/insights/', '/knowledge/',
            '/case-study', '/white-paper', '/report'
        ]):
            return 'article'
            
        # Standard pages
        return 'page'
    except:
        return 'page'

def get_pagination_urls(base_url, current_page_url):
    """Generate pagination URLs based on patterns"""
    pagination_urls = set()
    
    try:
        # Extract current page number
        page_match = re.search(r'/page/(\d+)', current_page_url)
        if page_match:
            current_page = int(page_match.group(1))
            # Add next few pages
            for page in range(current_page + 1, current_page + 4):
                next_url = re.sub(r'/page/\d+', f'/page/{page}', current_page_url)
                pagination_urls.add(next_url)
        else:
            # Check if URL ends with trailing slash
            if current_page_url.endswith('/'):
                pagination_urls.add(f"{current_page_url}page/2/")
            else:
                pagination_urls.add(f"{current_page_url}/page/2/")
    except Exception:
        pass
        
    return pagination_urls

def find_all_links(soup, base_url):
    """Find all valid links on page"""
    links = set()
    
    if not soup or not base_url:
        return links
    
    # Standard links
    if soup.find_all:  # Check if soup is valid
        # Standard links
        for a in soup.find_all('a', href=True):
            try:
                href = a.get('href')
                if href:
                    full_url = urljoin(base_url, href)
                    if is_valid_url(full_url):
                        links.add(full_url)
            except Exception:
                continue

        # Look for links in onclick events
        try:
            onclick_elements = soup.find_all(lambda tag: tag.get('onclick', ''))
            for element in onclick_elements:
                try:
                    onclick = element.get('onclick', '')
                    if onclick:
                        urls = re.findall(r'(?:href=|window\.location=|redirect\()[\'"](.*?)[\'"]', onclick)
                        for url in urls:
                            full_url = urljoin(base_url, url)
                            if is_valid_url(full_url):
                                links.add(full_url)
                except Exception:
                    continue
        except Exception:
            pass

        # Look for links in data attributes
        try:
            data_elements = soup.find_all(lambda tag: any(attr.startswith('data-') for attr in tag.attrs if attr))
            for element in data_elements:
                try:
                    for attr, value in element.attrs.items():
                        if attr.startswith('data-') and isinstance(value, str):
                            if value.startswith('http') or value.startswith('/'):
                                full_url = urljoin(base_url, value)
                                if is_valid_url(full_url):
                                    links.add(full_url)
                except Exception:
                    continue
        except Exception:
            pass

        # Additional link patterns
        try:
            # Look for pagination links
            pagination_elements = soup.find_all(class_=lambda x: x and ('pagination' in x or 'paging' in x))
            for element in pagination_elements:
                try:
                    for a in element.find_all('a', href=True):
                        href = a.get('href')
                        if href:
                            full_url = urljoin(base_url, href)
                            if is_valid_url(full_url):
                                links.add(full_url)
                except Exception:
                    continue
                    
            # Look for blog post links
            article_elements = soup.find_all(['article', 'div'], class_=lambda x: x and ('post' in x or 'article' in x))
            for element in article_elements:
                try:
                    for a in element.find_all('a', href=True):
                        href = a.get('href')
                        if href:
                            full_url = urljoin(base_url, href)
                            if is_valid_url(full_url):
                                links.add(full_url)
                except Exception:
                    continue
        except Exception:
            pass

    return links

def find_embedded_content(soup, content_map, base_url):
    """Find embedded content like images and documents"""
    try:
        if not soup or not base_url:
            return
            
        # Images
        for img in soup.find_all('img', src=True):
            try:
                src = img.get('src')
                if src:
                    full_url = urljoin(base_url, src)
                    if is_valid_url(full_url):
                        content_map['image'].add(full_url)
            except Exception:
                continue
        
        # Document links
        for a in soup.find_all('a', href=True):
            try:
                href = a.get('href')
                if href:
                    full_url = urljoin(base_url, href)
                    if any(ext in full_url.lower() for ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']):
                        content_map['document'].add(full_url)
            except Exception:
                continue
                    
        # Look for embedded content in iframes
        for iframe in soup.find_all('iframe', src=True):
            try:
                src = iframe.get('src')
                if src:
                    full_url = urljoin(base_url, src)
                    if is_valid_url(full_url):
                        content_type = classify_url(full_url)
                        content_map[content_type].add(full_url)
            except Exception:
                continue
                
    except Exception:
        pass

def handle_cookie_consent(driver):
    """Handle cookie consent popups"""
    common_selectors = [
        '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
        '.cookie-accept', '#accept-cookies',
        '[aria-label="Accept cookies"]',
        '#onetrust-accept-btn-handler',
        '.consent-accept', '.accept-all',
        '[data-testid="cookie-accept"]',
        'button[contains(text(), "Accept")]',
        'button[contains(text(), "I accept")]',
        'button[contains(text(), "Allow")]'
    ]
    
    for selector in common_selectors:
        try:
            accept_button = WebDriverWait(driver, 2).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
            )
            accept_button.click()
            time.sleep(1)
            return
        except:
            continue
            
def discover_site_content(driver, base_url, progress_bar):
    """Hybrid approach to content discovery"""
    content_map = {
        'page': set(),
        'article': set(),
        'document': set(),
        'image': set(),
        'api_endpoints': set()
    }
    
    processed_urls = set()
    to_process = {base_url}
    
    log_progress(progress_bar, "Starting hybrid content discovery...")
    
    # Initial basic crawl
    try:
        log_progress(progress_bar, "Attempting basic crawl...")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(base_url, headers=headers, timeout=10)
        if response.ok:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find initial links
            initial_links = find_all_links(soup, base_url)
            for link in initial_links:
                if link.startswith(base_url):
                    content_type = classify_url(link)
                    content_map[content_type].add(link)
                    if content_type in ['page', 'article']:
                        to_process.add(link)
            
            # Find embedded content
            find_embedded_content(soup, content_map, base_url)
            log_progress(progress_bar, f"Basic crawl found {sum(len(v) for v in content_map.values())} items")
            
            # Try sitemap
            try:
                sitemap_url = urljoin(base_url, '/sitemap.xml')
                sitemap_response = requests.get(sitemap_url, timeout=5)
                if sitemap_response.ok:
                    sitemap_soup = BeautifulSoup(sitemap_response.text, 'xml')
                    for url in sitemap_soup.find_all('loc'):
                        if url.text.startswith(base_url):
                            content_type = classify_url(url.text)
                            content_map[content_type].add(url.text)
            except:
                pass
                
    except Exception as e:
        log_progress(progress_bar, f"Notice: Basic crawl - {str(e)}")
    
    # Use Selenium for dynamic content
    try:
        log_progress(progress_bar, "Starting dynamic content discovery...")
        driver.get(base_url)
        time.sleep(3)
        
        # Handle cookie consent
        handle_cookie_consent(driver)
        
        # Process initial dynamic content
        current_page_content = content_map.copy()
        scroll_count = 0
        last_height = driver.execute_script("return document.body.scrollHeight")
        
        while scroll_count < 5:  # Maximum scroll attempts
            # Scroll and wait
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            
            # Click any load more buttons
            load_more_selectors = [
                ".load-more", "#load-more", "[class*='load-more']",
                "button:contains('Load More')", "a:contains('Load More')",
                "[class*='infinite-scroll']", ".next", ".more",
                ".pagination", ".next-page", "[aria-label*='next']"
            ]
            
            for selector in load_more_selectors:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        if element.is_displayed():
                            driver.execute_script("arguments[0].click();", element)
                            time.sleep(2)
                except:
                    continue
            
            # Get new content
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            new_links = find_all_links(soup, base_url)
            
            # Process new links
            for link in new_links:
                if link.startswith(base_url):
                    content_type = classify_url(link)
                    content_map[content_type].add(link)
                    if content_type in ['page', 'article']:
                        to_process.add(link)
            
            # Update embedded content
            find_embedded_content(soup, content_map, base_url)
            
            # Check scroll progress
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                scroll_count += 1
            else:
                scroll_count = 0
                last_height = new_height
            
            # Check for new content
            total_current = sum(len(v) for v in current_page_content.values())
            total_new = sum(len(v) for v in content_map.values())
            
            if total_new > total_current:
                current_page_content = content_map.copy()
                log_progress(progress_bar, f"Found {total_new} items after scroll {5 - scroll_count}")
            
    except Exception as e:
        log_progress(progress_bar, f"Notice: Dynamic discovery - {str(e)}")
    
    # Process pagination if found
    for url in list(to_process):  # Convert to list to avoid modification during iteration
        if url not in processed_urls:
            pagination_urls = get_pagination_urls(base_url, url)
            for pagination_url in pagination_urls:
                content_type = classify_url(pagination_url)
                content_map[content_type].add(pagination_url)
    
    # Clean and deduplicate results
    for content_type in content_map:
        content_map[content_type] = list(set(url for url in content_map[content_type] 
            if is_valid_url(url) and not is_unwanted_link(url, base_url)))
    
    total_items = sum(len(v) for v in content_map.values())
    log_progress(progress_bar, f"Content discovery complete. Found {total_items} total items")
    
    return content_map

def extract_content(driver, url, content_type, base_url, exclude_types):
    """Extract content based on content type"""
    content = []
    
    try:
        driver.get(url)
        time.sleep(2)
        
        if content_type == 'document':
            content.append(f"[DOCUMENT] {url}\n")
            return content
            
        if content_type == 'image':
            content.append(f"[IMAGE] {url}\n")
            return content
        
        # For articles and pages
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Try schema.org markup first
        schema_elements = soup.find_all('script', type='application/ld+json')
        for element in schema_elements:
            try:
                data = json.loads(element.string)
                if isinstance(data, dict):
                    if 'articleBody' in data:
                        content.append(f"[ARTICLE_BODY] {clean_text(data['articleBody'])}\n")
                    if 'description' in data:
                        content.append(f"[DESCRIPTION] {clean_text(data['description'])}\n")
            except:
                continue
        
        # Main content extraction
        main_content_selectors = [
            'article', 'main', '.content', '.post-content',
            '[role="main"]', '.entry-content', '.article-content',
            '.post-body', '.blog-post', '.article-body'
        ]
        
        content_found = False
        for selector in main_content_selectors:
            try:
                main_content = soup.select_one(selector)
                if main_content:
                    content_found = True
                    for element in main_content.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li', 'a']):
                        if should_exclude(element):
                            continue
                            
                        if element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li']:
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
                    if content:
                        break
            except:
                continue
        
        # If no content found in main content areas, try general content
        if not content_found:
            for element in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li', 'a']):
                if should_exclude(element):
                    continue
                    
                if element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li']:
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
        log_progress(progress_bar, f"Notice: Content extraction - {str(e)}")
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
        
        if not any(content_map.values()):
            log_progress(progress_bar, "No content discovered. Please check the URL.")
            return []
            
        # Process discovered content
        urls_to_process = []
        for content_type, urls in content_map.items():
            for url in urls:
                urls_to_process.append((url, content_type))
        
        if max_urls:
            urls_to_process = urls_to_process[:max_urls]
        
        total = len(urls_to_process)
        log_progress(progress_bar, f"Processing {total} discovered items...")
        
        # Process each URL
        for i, (url, content_type) in enumerate(urls_to_process, 1):
            try:
                log_progress(progress_bar, f"Processing {i}/{total}: {url}")
                content = extract_content(driver, url, content_type, base_url, exclude_types)
                if content:
                    all_content.extend([f"\n[URL] {url}\n"])
                    all_content.extend(content)
                    st.session_state.scraped_urls.append(url)
            except Exception as e:
                log_progress(progress_bar, f"Notice: URL processing - {str(e)}")
                continue
                
    except Exception as e:
        log_progress(progress_bar, f"Notice: Scraping process - {str(e)}")
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