import streamlit as st
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from urllib.parse import urljoin, urlparse
import re
import hashlib
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

def create_chrome_options():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
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

class ContentLoadingStrategy:
    def is_applicable(self, driver):
        raise NotImplementedError
    
    def execute(self, driver, base_url):
        raise NotImplementedError

class PaginationStrategy(ContentLoadingStrategy):
    def is_applicable(self, driver):
        pagination_selectors = [
            "button.archive__pagination__number",
            "a.next",
            ".pagination .next",
            "[aria-label='Next page']"
        ]
        for selector in pagination_selectors:
            if driver.find_elements(By.CSS_SELECTOR, selector):
                return True
        return False

    def execute(self, driver, base_url):
        pagination_selectors = [
            "button.archive__pagination__number",
            "a.next",
            ".pagination .next",
            "[aria-label='Next page']"
        ]
        for selector in pagination_selectors:
            buttons = driver.find_elements(By.CSS_SELECTOR, selector)
            if buttons:
                current_page = next(
                    (btn for btn in buttons if 'active' in btn.get_attribute('class').split()),
                    None
                )
                if current_page:
                    current_num = int(current_page.text)
                    next_button = next(
                        (btn for btn in buttons if btn.text.isdigit() and int(btn.text) == current_num + 1),
                        None
                    )
                    if next_button and not next_button.get_attribute('disabled'):
                        driver.execute_script("arguments[0].scrollIntoView(true);", next_button)
                        time.sleep(1)
                        driver.execute_script("arguments[0].click();", next_button)
                        time.sleep(3)
                        return gather_page_content(driver, base_url)
        return []

class InfiniteScrollStrategy(ContentLoadingStrategy):
    def is_applicable(self, driver):
        # Infinite scroll is hard to detect reliably, so we'll always try it
        return True

    def execute(self, driver, base_url):
        last_height = driver.execute_script("return document.body.scrollHeight")
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height > last_height:
            return gather_page_content(driver, base_url)
        return []

class LoadMoreButtonStrategy(ContentLoadingStrategy):
    def is_applicable(self, driver):
        load_more_selectors = [
            ".load-more",
            "#load-more",
            "[aria-label='Load more']",
            "button:contains('Load More')",
            "a:contains('Load More')"
        ]
        for selector in load_more_selectors:
            if driver.find_elements(By.CSS_SELECTOR, selector):
                return True
        return False

    def execute(self, driver, base_url):
        load_more_selectors = [
            ".load-more",
            "#load-more",
            "[aria-label='Load more']",
            "button:contains('Load More')",
            "a:contains('Load More')"
        ]
        for selector in load_more_selectors:
            load_more = driver.find_elements(By.CSS_SELECTOR, selector)
            if load_more:
                for button in load_more:
                    if button.is_displayed():
                        driver.execute_script("arguments[0].click();", button)
                        time.sleep(3)
                        return gather_page_content(driver, base_url)
        return []

def gather_page_content(driver, base_url):
    links = []
    
    # Strategy 1: Article cards
    articles = driver.find_elements(By.CSS_SELECTOR, "article.c-article, div.article, .post, .blog-post")
    for article in articles:
        try:
            link = article.find_element(By.CSS_SELECTOR, "a.card-title, h2 a, h3 a, .title a").get_attribute('href')
            if link and is_valid_url(link) and link.startswith(base_url):
                links.append(link)
        except:
            continue

    # Strategy 2: General article links 
    article_links = driver.find_elements(By.TAG_NAME, 'a')
    for link in article_links:
        try:
            href = link.get_attribute('href')
            if href and is_valid_url(href) and href.startswith(base_url):
                if any(pattern in href.lower() for pattern in ['/article/', '/blog/', '/post/', '/news/']):
                    links.append(href)
        except:
            continue

    return list(set(links))

def load_more_content(driver, base_url, strategies):
    all_links = []
    strategy_results = []
    
    for strategy in strategies:
        if strategy.is_applicable(driver):
            try:
                new_links = strategy.execute(driver, base_url)
                if new_links:
                    all_links.extend(new_links)
                    strategy_results.append(f"{strategy.__class__.__name__}: Success ({len(new_links)} links)")
                    break
            except Exception as e:
                strategy_results.append(f"{strategy.__class__.__name__}: Failed - {str(e)}")
    
    return list(set(all_links)), strategy_results

def extract_content(driver, base_url, exclude_types):
    content = []
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    
    content_selectors = [
        "article", ".post-content", ".entry-content", 
        "main", "#main-content", ".main-content",
        ".content", "#content"
    ]
    
    main_content = None
    for selector in content_selectors:
        main_content = soup.select_one(selector)
        if main_content:
            break
    
    if not main_content:
        main_content = soup.body
    
    for element in main_content.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'a', 'img']):
        if should_exclude(element):
            continue

        if element.name in ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li']:
            if 'text' not in exclude_types:
                text = clean_text(element.get_text())
                if text and len(text) > 20:
                    content.append(f"[{element.name.upper()}] {text}")
        elif element.name == 'a' and 'links' not in exclude_types:
            href = element.get('href')
            if href:
                if href.startswith(('http', 'https')):
                    content.append(f"[EXTERNAL LINK] {href}")
                elif href.startswith('/'):
                    content.append(f"[INTERNAL LINK] {urljoin(base_url, href)}")
        elif element.name == 'img' and 'images' not in exclude_types:
            src = element.get('src')
            alt = element.get('alt', '')
            if src:
                content.append(f"[IMAGE] URL: {urljoin(base_url, src)}, Alt: {alt}")

    return content

def scrape_single_page(url, base_url, exclude_types):
    options = create_chrome_options()
    driver = webdriver.Chrome(service=Service(), options=options)
    try:
        driver.get(url)
        time.sleep(2)
        return extract_content(driver, base_url, exclude_types)
    finally:
        driver.quit()

def adaptive_crawl(base_url, max_depth, max_urls, exclude_types, strategies, progress_bar):
    options = create_chrome_options()
    driver = webdriver.Chrome(service=Service(), options=options)
    
    try:
        driver.get(base_url)
        time.sleep(2)
        initial_links, strategy_results = load_more_content(driver, base_url, strategies)
    except Exception as e:
        st.warning(f"Dynamic content loading failed: {str(e)}. Falling back to basic scraping.")
        initial_links = [base_url]
        strategy_results = ["Fallback: Basic scraping"]
    finally:
        driver.quit()
    
    for result in strategy_results:
        st.info(result)
    
    to_visit = [(url, 0) for url in initial_links]
    visited = set()
    all_content = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_url = {}
        
        while to_visit and len(visited) < max_urls:
            url, depth = to_visit.pop(0)
            if url not in visited and depth <= max_depth:
                visited.add(url)
                future_to_url[executor.submit(scrape_single_page, url, base_url, exclude_types)] = url

                if len(future_to_url) >= 5 or not to_visit:
                    for future in as_completed(future_to_url):
                        url = future_to_url[future]
                        try:
                            content = future.result()
                            progress_bar.text(f"Scraped: {url}")
                            all_content.extend([f"\n[URL] {url}\n"])
                            all_content.extend(content)
                            
                            if depth < max_depth:
                                new_links = gather_page_content(driver, base_url)
                                to_visit.extend((link, depth + 1) for link in new_links if link not in visited)
                        except Exception as e:
                            st.error(f"Error scraping {url}: {str(e)}")
                    
                    future_to_url.clear()
            
            # Reorder to_visit to balance depth and breadth
            to_visit.sort(key=lambda x: x[1])

    return all_content

def main():
    st.title("Advanced Web Scraper for Competitor Analysis")

    url = st.text_input("Enter the website URL to scrape:")
    max_depth = st.number_input("Enter the maximum depth to scrape:", min_value=0, max_value=5, value=1, step=1)
    max_urls = st.number_input("Maximum number of URLs to scrape (leave blank for no limit):", min_value=1, value=None)
    
    exclude_types = st.multiselect(
        "Select content types to exclude:",
        ['text', 'links', 'images', 'blog posts'],
        default=[]
    )
    
    st.subheader("Advanced Settings")
    with st.expander("Content Loading Strategies"):
        use_pagination = st.checkbox("Use Pagination", value=True)
        use_infinite_scroll = st.checkbox("Use Infinite Scroll", value=True)
        use_load_more = st.checkbox("Use Load More Buttons", value=True)
    
    if st.button("Scrape"):
        if not is_valid_url(url):
            st.error("Please enter a valid URL.")
            return
        
        st.info("Scraping in progress...")
        progress_bar = st.empty()
        
        strategies = []
        if use_pagination:
            strategies.append(PaginationStrategy())
        if use_infinite_scroll:
            strategies.append(InfiniteScrollStrategy())
        if use_load_more:
            strategies.append(LoadMoreButtonStrategy())
        
        try:
            content = adaptive_crawl(url, max_depth, max_urls, exclude_types, strategies, progress_bar)
            
            if content:
                filename = f"{urlparse(url).netloc}_analysis.txt"
                with open(filename, "w", encoding="utf-8") as f:
                    for line in content:
                        if 'blog posts' in exclude_types and is_blog_post(line):
                            continue
                        f.write(line + "\n")
                
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
                st.text_area("Content Preview", value="\n".join(content[:20]), height=300)
            else:
                st.warning("No content could be extracted. Please check the URL and try again.")
            
        except Exception as e:
            st.error(f"Error during scraping: {str(e)}")

if __name__ == "__main__":
    main()