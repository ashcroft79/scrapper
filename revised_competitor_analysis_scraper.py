import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import hashlib

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

def extract_content(soup, base_url, exclude_types):
    content = []
    seen_content = set()
    
    for element in soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'a', 'img']):
        if should_exclude(element):
            continue

        if element.name in ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li']:
            if 'text' in exclude_types:
                continue
            text = clean_text(element.get_text())
            if text and len(text) > 20:
                content_hash = hashlib.md5(text.encode()).hexdigest()
                if content_hash not in seen_content:
                    seen_content.add(content_hash)
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

def scrape_page(url, depth, max_depth, visited, exclude_types):
    if depth > max_depth or url in visited:
        return []

    visited.add(url)
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        content = extract_content(soup, url, exclude_types)
        content.insert(0, f"\n[URL] {url}\n")
        
        if depth < max_depth:
            for link in soup.find_all('a', href=True):
                next_url = urljoin(url, link['href'])
                if is_valid_url(next_url) and urlparse(next_url).netloc == urlparse(url).netloc:
                    content.extend(scrape_page(next_url, depth + 1, max_depth, visited, exclude_types))
        
        return content
    
    except Exception as e:
        st.error(f"Error scraping {url}: {str(e)}")
        return []

def main():
    st.title("Advanced Web Scraper for Competitor Analysis")

    url = st.text_input("Enter the website URL to scrape:")
    max_depth = st.number_input("Enter the maximum depth to scrape:", min_value=0, max_value=5, value=1, step=1)
    
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
        content = scrape_page(url, 0, max_depth, set(), exclude_types)
        
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
            st.warning("No content could be extracted. The website might be using JavaScript to load content, which this scraper can't process.")

if __name__ == "__main__":
    main()
