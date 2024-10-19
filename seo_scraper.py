from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
import json
import re
from urllib.parse import urljoin, urlparse
import time

def setup_network_monitoring(driver):
    """Enable network monitoring in Chrome"""
    driver.execute_cdp_cmd('Network.enable', {})
    requests = []
    
    def capture_request(request):
        requests.append(request)
    
    driver.execute_cdp_cmd('Network.setRequestInterception', {'patterns': [{'urlPattern': '*'}]})
    driver.on('Network.requestIntercepted', capture_request)
    
    return requests

def analyze_network_requests(requests, base_url):
    """Analyze network requests to find content endpoints"""
    content_endpoints = []
    
    # Common patterns in content API endpoints
    api_patterns = [
        r'/api/content',
        r'/api/articles',
        r'/api/posts',
        r'/wp-json/wp/v2',
        r'/load-more',
        r'page=\d+',
        r'offset=\d+',
        r'limit=\d+'
    ]
    
    for request in requests:
        url = request.get('url', '')
        if any(re.search(pattern, url) for pattern in api_patterns):
            content_endpoints.append(url)
            
    return content_endpoints

def find_pagination_info(driver):
    """Find pagination information using various methods"""
    pagination_info = {
        'next_links': [],
        'total_pages': None,
        'current_page': None
    }
    
    # Method 1: Check rel="next" links
    try:
        next_links = driver.find_elements(By.CSS_SELECTOR, 'link[rel="next"]')
        pagination_info['next_links'].extend([link.get_attribute('href') for link in next_links])
    except:
        pass
        
    # Method 2: Check sitemap for pagination patterns
    try:
        sitemap_url = urljoin(driver.current_url, '/sitemap.xml')
        response = requests.get(sitemap_url)
        if response.ok:
            soup = BeautifulSoup(response.text, 'xml')
            urls = soup.find_all('url')
            page_pattern = re.compile(r'page/\d+|page=\d+')
            pagination_info['next_links'].extend([
                url.loc.text for url in urls 
                if page_pattern.search(url.loc.text)
            ])
    except:
        pass
    
    # Method 3: Look for page numbers in DOM
    try:
        pagination_elements = driver.find_elements(By.CSS_SELECTOR, '.pagination, .nav-links, .pager')
        for element in pagination_elements:
            numbers = re.findall(r'\d+', element.text)
            if numbers:
                pagination_info['total_pages'] = max(map(int, numbers))
                current = element.find_element(By.CSS_SELECTOR, '.current, .active')
                if current:
                    pagination_info['current_page'] = int(current.text)
    except:
        pass
        
    return pagination_info

def extract_dynamic_content(driver):
    """Extract content that may be loaded dynamically"""
    content = []
    
    # Monitor network requests
    requests = setup_network_monitoring(driver)
    
    # Initial scroll to trigger content loading
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(2)
    
    # Analyze network requests
    content_endpoints = analyze_network_requests(requests, driver.current_url)
    
    # Directly fetch content from APIs if found
    for endpoint in content_endpoints:
        try:
            response = requests.get(endpoint)
            if response.ok:
                data = response.json()
                # Extract content from API response
                content.extend(parse_api_response(data))
        except:
            continue
    
    # Find pagination information
    pagination = find_pagination_info(driver)
    
    # Handle regular pagination if available
    if pagination['next_links']:
        for link in pagination['next_links']:
            try:
                driver.get(link)
                content.extend(extract_content(driver))
            except:
                continue
                
    # If no pagination found, try infinite scroll simulation
    elif not content_endpoints:
        last_height = driver.execute_script("return document.body.scrollHeight")
        while True:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height
            
    return content

def parse_api_response(data):
    """Parse content from API responses"""
    content = []
    
    # Common patterns in API responses
    if isinstance(data, list):
        for item in data:
            content.extend(extract_from_item(item))
    elif isinstance(data, dict):
        if 'data' in data:
            content.extend(parse_api_response(data['data']))
        else:
            content.extend(extract_from_item(data))
            
    return content

def extract_from_item(item):
    """Extract content from individual API response items"""
    content = []
    
    # Common field names for content
    content_fields = ['title', 'content', 'excerpt', 'description', 'text']
    link_fields = ['url', 'link', 'permalink']
    
    for field in content_fields:
        if field in item and isinstance(item[field], str):
            content.append(f"[{field.upper()}] {item[field]}")
            
    for field in link_fields:
        if field in item and isinstance(item[field], str):
            content.append(f"[LINK] {item[field]}")
            
    return content

# Add these functions to your existing scraper
def load_more_content(driver, base_url):
    """Enhanced content loading with search engine techniques"""
    all_links = []
    
    # First try API/network monitoring approach
    content = extract_dynamic_content(driver)
    
    # Extract links from dynamically loaded content
    for item in content:
        if item.startswith('[LINK]'):
            link = item.split('[LINK]')[1].strip()
            if link.startswith(base_url):
                all_links.append(link)
    
    # If no links found, fall back to existing methods
    if not all_links:
        all_links = gather_page_content(driver, base_url)
        
    return list(set(all_links))