import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import json
from collections import defaultdict
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

def extract_content(soup, base_url, include_blog_posts):
    content = defaultdict(list)
    seen_content = set()
    
    for element in soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'a', 'img']):
        if should_exclude(element):
            continue

        if element.name in ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li']:
            text = clean_text(element.get_text())
            if text and len(text) > 20:
                content_hash = hashlib.md5(text.encode()).hexdigest()
                if content_hash not in seen_content:
                    seen_content.add(content_hash)
                    if element.name.startswith('h'):
                        content['headers'].append(text)
                    else:
                        content['paragraphs'].append(text)
        elif element.name == 'a':
            href = element.get('href')
            if href:
                if href.startswith(('http', 'https')):
                    content['external_links'].append(href)
                elif href.startswith('/'):
                    content['internal_links'].append(urljoin(base_url, href))
        elif element.name == 'img':
            src = element.get('src')
            alt = element.get('alt', '')
            if src:
                content['images'].append({
                    'url': urljoin(base_url, src),
                    'alt_text': alt
                })

    if not include_blog_posts:
        content['paragraphs'] = [p for p in content['paragraphs'] if not is_blog_post(p)]

    return content

def is_blog_post(text):
    # This is a simple heuristic. You might want to refine this based on your specific needs.
    blog_keywords = ['blog', 'post', 'article', 'news']
    return any(keyword in text.lower() for keyword in blog_keywords)

def scrape_page(url, depth, max_depth, visited, include_blog_posts):
    if depth > max_depth or url in visited:
        return {}

    visited.add(url)
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        content = extract_content(soup, url, include_blog_posts)
        content['url'] = url
        
        if depth < max_depth:
            for link in soup.find_all('a', href=True):
                next_url = urljoin(url, link['href'])
                if is_valid_url(next_url) and urlparse(next_url).netloc == urlparse(url).netloc:
                    sub_content = scrape_page(next_url, depth + 1, max_depth, visited, include_blog_posts)
                    for key, value in sub_content.items():
                        if isinstance(value, list):
                            content[key].extend(value)
                        elif key not in content:
                            content[key] = value
        
        return content
    
    except Exception as e:
        st.error(f"Error scraping {url}: {str(e)}")
        return {}

def main():
    st.title("Advanced Web Scraper for Competitor Analysis")

    # Initialize session state
    if 'content' not in st.session_state:
        st.session_state.content = None
    if 'selected_content' not in st.session_state:
        st.session_state.selected_content = defaultdict(list)

    url = st.text_input("Enter the website URL to scrape:")
    max_depth = st.number_input("Enter the maximum depth to scrape:", min_value=0, max_value=5, value=1, step=1)
    include_blog_posts = st.checkbox("Include blog posts", value=False)
    
    if st.button("Scrape"):
        if not is_valid_url(url):
            st.error("Please enter a valid URL.")
            return
        
        st.info("Scraping in progress...")
        st.session_state.content = scrape_page(url, 0, max_depth, set(), include_blog_posts)
        st.session_state.selected_content = defaultdict(list)
        
    if st.session_state.content:
        st.subheader("Extracted Content Summary")
        st.write(f"Total headers: {len(st.session_state.content['headers'])}")
        st.write(f"Total paragraphs: {len(st.session_state.content['paragraphs'])}")
        st.write(f"Total external links: {len(st.session_state.content['external_links'])}")
        st.write(f"Total internal links: {len(st.session_state.content['internal_links'])}")
        st.write(f"Total images: {len(st.session_state.content['images'])}")
        
        st.subheader("Content Selection")
        st.write("Select the content you want to include in the output. You can select multiple items in each category.")
        
        for key in ['headers', 'paragraphs', 'external_links', 'internal_links']:
            st.session_state.selected_content[key] = st.multiselect(
                f"Select {key} to include:",
                st.session_state.content[key],
                default=st.session_state.selected_content.get(key, [])
            )
        
        st.session_state.selected_content['images'] = st.multiselect(
            "Select images to include:",
            [img['url'] for img in st.session_state.content['images']],
            default=st.session_state.selected_content.get('images', [])
        )
        
        if st.button("Generate Output"):
            filename = f"{urlparse(url).netloc}_analysis.json"
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(st.session_state.selected_content, f, ensure_ascii=False, indent=2)
            
            st.success(f"Analysis completed! Selected content saved to {filename}")
            st.download_button(
                label="Download Selected Content",
                data=json.dumps(st.session_state.selected_content, ensure_ascii=False, indent=2),
                file_name=filename,
                mime="application/json"
            )

if __name__ == "__main__":
    main()