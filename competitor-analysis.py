import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import os

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

def extract_content(soup, base_url):
    content = []
    resources = []

    for element in soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'a', 'img']):
        if not should_exclude(element):
            if element.name in ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li']:
                text = clean_text(element.get_text())
                if text and len(text) > 20:
                    content.append(text)
            elif element.name == 'a':
                href = element.get('href')
                if href and href.endswith(('.pdf', '.doc', '.docx', '.xls', '.xlsx')):
                    resources.append(f"Document: {urljoin(base_url, href)}")
            elif element.name == 'img':
                src = element.get('src')
                alt = element.get('alt', '')
                if src:
                    resources.append(f"Image: {urljoin(base_url, src)} - {alt}")

    return content, resources

def get_company_name(url):
    parsed_url = urlparse(url)
    return parsed_url.netloc.split('.')[-2]

def scrape_page(url, depth, max_depth, visited):
    if depth > max_depth or url in visited:
        return [], []

    visited.add(url)
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        content, resources = extract_content(soup, url)
        
        if depth < max_depth:
            for link in soup.find_all('a', href=True):
                next_url = urljoin(url, link['href'])
                if is_valid_url(next_url) and urlparse(next_url).netloc == urlparse(url).netloc:
                    sub_content, sub_resources = scrape_page(next_url, depth + 1, max_depth, visited)
                    content.extend(sub_content)
                    resources.extend(sub_resources)
        
        return content, resources
    
    except Exception as e:
        st.error(f"Error scraping {url}: {str(e)}")
        return [], []

def main():
    st.title("Competitor Analysis Web Scraper")

    url = st.text_input("Enter the website URL to scrape:")
    max_depth = st.number_input("Enter the maximum depth to scrape:", min_value=0, max_value=2, value=1, step=1)
    
    if st.button("Scrape"):
        if not is_valid_url(url):
            st.error("Please enter a valid URL.")
            return
        
        st.info("Scraping in progress...")
        content, resources = scrape_page(url, 0, max_depth, set())
        
        if content or resources:
            st.subheader("Extracted Content Preview")
            for item in content[:10]:
                st.write(f"- {item}")
            
            if len(content) > 10:
                st.write(f"... and {len(content) - 10} more content items.")

            st.subheader("Extracted Resources Preview")
            for item in resources[:10]:
                st.write(f"- {item}")
            
            if len(resources) > 10:
                st.write(f"... and {len(resources) - 10} more resource items.")
        else:
            st.warning("No content could be extracted. The website might be using JavaScript to load content, which this scraper can't process.")
        
        company_name = get_company_name(url)
        filename = f"{company_name}_analysis.txt"
        
        with open(filename, "w", encoding="utf-8") as f:
            f.write("Extracted Content:\n\n")
            for item in content:
                f.write(f"{item}\n\n")
            f.write("\nExtracted Resources:\n\n")
            for item in resources:
                f.write(f"{item}\n")
        
        st.success(f"Analysis completed! Content saved to {filename}")
        
        with open(filename, "r", encoding="utf-8") as f:
            file_content = f.read()
        
        st.download_button(label="Download Analysis", data=file_content, file_name=filename, mime="text/plain")

if __name__ == "__main__":
    main()