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
from itertools import islice
import queue
import threading

def create_chrome_options():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    # Performance optimizations
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-infobars')
    options.add_argument('--disable-notifications')
    options.add_argument('--disable-popup-blocking')
    options.page_load_strategy = 'eager'  # Don't wait for all resources
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    prefs = {
        'profile.managed_default_content_settings.images': 2,  # Don't load images
        'disk-cache-size': 4096,  # Increase cache size
        'profile.managed_default_content_settings.javascript': 1  # Enable JavaScript
    }
    options.add_experimental_option('prefs', prefs)
    return options

def create_session():
    """Create a requests session with optimized settings"""
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=100,
        pool_maxsize=100,
        max_retries=3,
        pool_block=False
    )
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive',
        'DNT': '1'
    })
    return session

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
        '/register', '/signup', '/cart', '/checkout', '/account',
        '/wp-admin', '/wp-login', '/feed', '/rss', '/xmlrpc.php'
    ]
    
    try:
        is_external = not url.startswith(base_url)
        is_unwanted = any(pattern in url.lower() for pattern in unwanted_patterns)
        has_fragment = '#' in url
        return is_external or is_unwanted or has_fragment
    except:
        return True

def log_progress(progress_bar, message):
    """Thread-safe progress logging"""
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

def parallel_initial_discovery(url, session):
    """Parallel processing for initial content discovery"""
    try:
        response = session.get(url, timeout=5)
        if response.ok:
            soup = BeautifulSoup(response.text, 'html.parser')
            links = find_all_links(soup, url)
            content_type = classify_url(url)
            return {
                'url': url,
                'links': links,
                'content_type': content_type,
                'success': True
            }
    except:
        pass
    return {'url': url, 'links': set(), 'content_type': None, 'success': False}

def find_all_links(soup, base_url):
    """Optimized link discovery"""
    links = set()
    
    if not soup or not base_url:
        return links
    
    if soup.find_all:  # Check if soup is valid
        # Use a set for faster lookups
        processed_links = set()
        
        # Process all links in one pass
        for a in soup.find_all(['a', 'link'], href=True):
            try:
                href = a.get('href')
                if href and href not in processed_links:
                    processed_links.add(href)
                    full_url = urljoin(base_url, href)
                    if is_valid_url(full_url):
                        links.add(full_url)
            except:
                continue

    return links

def find_embedded_content(soup, content_map, base_url):
    """Find embedded content like images and documents"""
    if not soup or not base_url:
        return
        
    # Process all relevant tags in one pass
    for tag in soup.find_all(['img', 'a', 'iframe']):
        try:
            if tag.name == 'img' and tag.get('src'):
                full_url = urljoin(base_url, tag['src'])
                if is_valid_url(full_url):
                    content_map['image'].add(full_url)
            elif tag.name == 'a' and tag.get('href'):
                href = tag['href']
                if any(ext in href.lower() for ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']):
                    full_url = urljoin(base_url, href)
                    if is_valid_url(full_url):
                        content_map['document'].add(full_url)
            elif tag.name == 'iframe' and tag.get('src'):
                full_url = urljoin(base_url, tag['src'])
                if is_valid_url(full_url):
                    content_type = classify_url(full_url)
                    content_map[content_type].add(full_url)
        except:
            continue

def handle_cookie_consent(driver):
    """Quick cookie consent handling"""
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
            WebDriverWait(driver, 1).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
            ).click()
            return
        except:
            continue
            
class DriverPool:
    """Thread-safe pool of WebDriver instances"""
    def __init__(self, size=3):
        self.drivers = queue.Queue()
        self.size = size
        for _ in range(size):
            self.drivers.put(webdriver.Chrome(service=Service(), options=create_chrome_options()))
            
    def get_driver(self):
        return self.drivers.get()
        
    def return_driver(self, driver):
        self.drivers.put(driver)
        
    def quit_all(self):
        while not self.drivers.empty():
            driver = self.drivers.get()
            try:
                driver.quit()
            except:
                pass

def discover_site_content(driver_pool, base_url, progress_bar):
    """Optimized hybrid approach to content discovery"""
    content_map = {
        'page': set(),
        'article': set(),
        'document': set(),
        'image': set(),
        'api_endpoints': set()
    }
    
    processed_urls = set()
    to_process = {base_url}
    batch_size = 10  # Increased batch size for parallel processing
    
    log_progress(progress_bar, "Starting optimized content discovery...")
    
    # Create session for parallel requests
    session = create_session()
    
    # Initial parallel discovery
    with ThreadPoolExecutor(max_workers=10) as executor:
        while to_process:
            batch = list(islice(to_process, batch_size))
            to_process = to_process - set(batch)
            
            future_to_url = {
                executor.submit(parallel_initial_discovery, url, session): url 
                for url in batch
            }
            
            for future in as_completed(future_to_url):
                result = future.result()
                if result['success']:
                    content_type = result['content_type']
                    if content_type:
                        content_map[content_type].add(result['url'])
                    
                    # Add new URLs to process
                    new_urls = {url for url in result['links'] 
                              if url.startswith(base_url) 
                              and url not in processed_urls
                              and not is_unwanted_link(url, base_url)}
                    to_process.update(new_urls)
                
                processed_urls.add(result['url'])
    
    # Dynamic content discovery with multiple drivers
    try:
        log_progress(progress_bar, "Starting dynamic content discovery...")
        driver = driver_pool.get_driver()
        
        try:
            driver.get(base_url)
            WebDriverWait(driver, 2).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            
            handle_cookie_consent(driver)
            
            # Optimized scroll and check
            scroll_count = 0
            last_height = driver.execute_script("return document.body.scrollHeight")
            content_hash = ""
            
            while scroll_count < 3:
                # Smooth scroll with dynamic wait
                driver.execute_script("""
                    window.scrollTo({
                        top: document.body.scrollHeight,
                        behavior: 'smooth'
                    });
                """)
                time.sleep(1)
                
                # Quick check for new content
                page_content = driver.page_source
                new_hash = hashlib.md5(page_content.encode()).hexdigest()
                
                if new_hash != content_hash:
                    content_hash = new_hash
                    scroll_count = 0
                    
                    # Process new content
                    soup = BeautifulSoup(page_content, 'html.parser')
                    new_links = find_all_links(soup, base_url)
                    
                    for link in new_links:
                        if link.startswith(base_url) and link not in processed_urls:
                            content_type = classify_url(link)
                            content_map[content_type].add(link)
                else:
                    scroll_count += 1
                
                # Check for dynamic loading triggers
                for trigger in ['.load-more', '.infinite-scroll', '.pagination']:
                    try:
                        elements = driver.find_elements(By.CSS_SELECTOR, trigger)
                        for element in elements:
                            if element.is_displayed():
                                driver.execute_script("arguments[0].click();", element)
                                time.sleep(1)
                    except:
                        continue
                
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    scroll_count += 1
                else:
                    last_height = new_height
        finally:
            driver_pool.return_driver(driver)
            
    except Exception as e:
        log_progress(progress_bar, f"Notice: Dynamic discovery - {str(e)}")
    
    return content_map

def extract_content(driver_pool, url, content_type, base_url, exclude_types):
    """Optimized content extraction"""
    content = []
    driver = driver_pool.get_driver()
    
    try:
        driver.get(url)
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        
        if content_type == 'document':
            content.append(f"[DOCUMENT] {url}\n")
            return content
            
        if content_type == 'image':
            content.append(f"[IMAGE] {url}\n")
            return content
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Use set for deduplication
        seen_content = set()
        
        # Process schema.org data
        for element in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(element.string)
                if isinstance(data, dict):
                    for key in ['articleBody', 'description']:
                        if key in data:
                            text = clean_text(data[key])
                            content_hash = hashlib.md5(text.encode()).hexdigest()
                            if content_hash not in seen_content:
                                seen_content.add(content_hash)
                                content.append(f"[{key.upper()}] {text}\n")
            except:
                continue
        
        # Process main content
        main_selectors = [
            'article', 'main', '.content', '.post-content',
            '[role="main"]', '.entry-content', '.article-content'
        ]
        
        for selector in main_selectors:
            try:
                main_content = soup.select_one(selector)
                if main_content:
                    for element in main_content.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li', 'a']):
                        if should_exclude(element):
                            continue
                            
                        if element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li']:
                            if 'text' not in exclude_types:
                                text = clean_text(element.get_text())
                                if text and len(text) > 20:
                                    content_hash = hashlib.md5(text.encode()).hexdigest()
                                    if content_hash not in seen_content:
                                        seen_content.add(content_hash)
                                        content.append(f"[{element.name.upper()}] {text}\n")
                        elif element.name == 'a' and 'links' not in exclude_types:
                            href = element.get('href')
                            if href:
                                full_url = urljoin(base_url, href)
                                if href.startswith(('http', 'https')):
                                    content.append(f"[EXTERNAL_LINK] {full_url}\n")
                                elif href.startswith('/'):
                                    content.append(f"[INTERNAL_LINK] {full_url}\n")
                    break
            except:
                continue
                
    except Exception as e:
        content.append(f"[ERROR] Failed to extract content: {str(e)}\n")
    finally:
        driver_pool.return_driver(driver)
        
    return content

def scrape_pages(base_url, initial_url, max_depth, exclude_types, max_urls, target_date, progress_bar):
    """Optimized main scraping function"""
    all_content = []
    driver_pool = DriverPool(size=3)  # Create pool of 3 drivers
    
    try:
        # Discover content
        content_map = discover_site_content(driver_pool, base_url, progress_bar)
        
        if not any(content_map.values()):
            log_progress(progress_bar, "No content discovered. Please check the URL.")
            return []
        
        # Process URLs in parallel
        urls_to_process = []
        for content_type, urls in content_map.items():
            for url in urls:
                urls_to_process.append((url, content_type))
        
        if max_urls:
            urls_to_process = urls_to_process[:max_urls]
        
        total = len(urls_to_process)
        log_progress(progress_bar, f"Processing {total} items in parallel...")
        
        processed_count = 0
        batch_size = 5
        
        with ThreadPoolExecutor(max_workers=3) as executor:
            for i in range(0, len(urls_to_process), batch_size):
                batch = urls_to_process[i:i + batch_size]
                futures = []
                
                for url, content_type in batch:
                    futures.append(
                        executor.submit(
                            extract_content,
                            driver_pool,
                            url,
                            content_type,
                            base_url,
                            exclude_types
                        )
                    )
                
                for future in as_completed(futures):
                    try:
                        content = future.result()
                        processed_count += 1
                        if content:
                            url = urls_to_process[processed_count - 1][0]
                            all_content.extend([f"\n[URL] {url}\n"])
                            all_content.extend(content)
                            st.session_state.scraped_urls.append(url)
                    except Exception as e:
                        log_progress(progress_bar, f"Notice: {str(e)}")
                        continue
                
                log_progress(progress_bar, f"Processed {min(i + batch_size, total)}/{total} items")
                
    finally:
        driver_pool.quit_all()
    
    return all_content

def main():
    st.title("Advanced Web Scraper for Competitor Analysis")

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