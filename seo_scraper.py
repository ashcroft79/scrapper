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
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

def create_chrome_options():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    # Enable CDP
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    return options

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

def gather_page_content(driver, base_url):
    """Gather content using multiple strategies"""
    links = set()
    
    # Strategy 1: Monitor network requests
    content_endpoints = analyze_browser_logs(driver)
    for endpoint in content_endpoints:
        try:
            response = requests.get(endpoint, timeout=5)
            if response.ok:
                try:
                    data = response.json()
                    # Look for URLs in JSON response
                    json_str = json.dumps(data)
                    url_pattern = rf'{base_url}[^"\']*'
                    found_urls = re.findall(url_pattern, json_str)
                    links.update(found_urls)
                except:
                    pass
        except:
            continue
    
    # Strategy 2: Check standard article elements
    selectors = [
        "article a", "div.post a", ".blog-post a",
        "h2 a", "h3 a", ".title a",
        "[class*='article'] a", "[class*='post'] a"
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
    
    # Strategy 3: Find schema.org markup
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

def load_more_content(driver, base_url):
    """Load content using multiple strategies"""
    all_links = set()
    page_unchanged_count = 0
    max_unchanged = 3  # Stop if content hasn't changed after 3 attempts
    
    while page_unchanged_count < max_unchanged:
        # Get current links
        current_links = set(gather_page_content(driver, base_url))
        new_links = current_links - all_links
        
        if not new_links:
            page_unchanged_count += 1
        else:
            page_unchanged_count = 0
            all_links.update(new_links)
        
        # Strategy 1: Check pagination info
        pagination = find_pagination_info(driver, base_url)
        if pagination['next_links']:
            for link in pagination['next_links'][:5]:  # Limit to prevent infinite loops
                try:
                    driver.get(link)
                    time.sleep(2)
                    all_links.update(gather_page_content(driver, base_url))
                except:
                    continue
            break  # Exit if we found and processed pagination
        
        # Strategy 2: Try infinite scroll
        last_height = driver.execute_script("return document.body.scrollHeight")
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)
        
        # Strategy 3: Look for load more buttons
        load_more_selectors = [
            ".load-more", "#load-more", "[class*='load-more']",
            "button:contains('Load More')", "a:contains('Load More')",
            "[class*='infinite-scroll']", ".next", ".more"
        ]
        
        for selector in load_more_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for element in elements:
                    if element.is_displayed():
                        driver.execute_script("arguments[0].click();", element)
                        time.sleep(3)
            except:
                continue
    
    return list(all_links)

# Keep your existing helper functions (is_valid_url, clean_text, should_exclude, etc.)

def extract_content(driver, base_url, exclude_types):
    """Extract content using multiple strategies"""
    content = []
    
    # Wait for dynamic content
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
    
    # Get page source after JavaScript execution
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    
    # Strategy 1: Check schema.org markup
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
    
    # Strategy 2: Standard content extraction
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
    
    options = create_chrome_options()
    driver = webdriver.Chrome(service=Service(), options=options)
    try:
        driver.get(initial_url)
        time.sleep(2)
        all_links = load_more_content(driver, base_url)
    finally:
        driver.quit()
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_url = {}
        for url in all_links:
            if max_urls is None or len(visited) < max_urls:
                if url not in visited and not is_unwanted_link(url, base_url):
                    visited.add(url)
                    future_to_url[executor.submit(scrape_single_page, url, base_url, exclude_types)] = url

        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                content = future.result()
                progress_bar.text(f"Scraped: {url}")
                st.session_state.scraped_urls.append(url)
                all_content.extend([f"\n[URL] {url}\n"])
                all_content.extend(content)
            except Exception as e:
                st.error(f"Error scraping {url}: {str(e)}")

    return all_content

# Your existing main() function remains the same

if __name__ == "__main__":
    main()