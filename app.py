import json
import re
import requests

def get_series_data_via_api(series_url):
    # 1. URL-ல் இருந்து Show ID-ஐ மட்டும் தனியாக உருவுதல்
    match = re.search(r'/show/([a-zA-Z0-9_-]+)', series_url)
    if not match:
        raise Exception("Invalid Show URL format.")
    
    show_id = match.group(1).split('?')[0]
    
    # 2. Pocket FM-ன் ஒரிஜினல் Internal API அல்லது Next.js Data Endpoint
    # மொபைல் ஆப் அல்லது ப்ராக்ஸி ஹெடர்களைப் பயன்படுத்துவது
    api_url = f"https://pocketfm.com/show/{show_id}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': series_url
    }
    
    try:
        # கிளவுட்ஃப்ளேரை ஏமாற்ற மொபைல் யூசர் ஏஜென்ட் பயன்படுத்துகிறோம்
        resp = requests.get(api_url, headers=headers, timeout=30)
        
        if resp.status_code == 403:
            raise Exception("Cloudflare blocked the request. Use Multi-Link Mode.")
            
        html = resp.text
        
        # 3. சீரிஸ் டைட்டிலை எடுப்பது
        title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        series_title = title_match.group(1).split("|")[0].strip() if title_match else "Pocket FM Series"
        
        # 4. __NEXT_DATA__ JSON-ஐ முழுமையாக உருவுவது
        json_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
        if not json_match:
            raise Exception("Could not extract Next.js JSON payload.")
            
        data = json.loads(json_match.group(1))
        
        # 5. ரிகர்சிவ் முறையில் அத்தனை எபிசோட் ID மற்றும் பெயர்களை எடுப்பது
        episodes_map = []
        
        def extract_episodes(node):
            if isinstance(node, dict):
                # எபிசோட் ஐடி மற்றும் டைட்டில் உள்ளே இருந்தால் அதைப் பிடிப்பது
                ep_id = node.get('episodeId') or node.get('id')
                ep_name = node.get('title') or node.get('name')
                
                if ep_id and isinstance(ep_id, str) and len(ep_id) > 10:
                    ep_url = f"https://pocketfm.com/episode/{ep_id}"
                    episodes_map.append({'id': ep_id, 'url': ep_url, 'title': ep_name if ep_name else f"Episode {ep_id}"})
                
                for k, v in node.items():
                    extract_episodes(v)
            elif isinstance(node, list):
                for item in node:
                    extract_episodes(item)
                    
        extract_episodes(data)
        
        # டூப்ளிகேட்களை நீக்கி தனித்துவமான லிங்க்களை மட்டும் தருதல்
        unique_episodes = {ep['url']: ep for ep in episodes_map}.values()
        ep_list = list(unique_episodes)
        
        if not ep_list:
            raise Exception("Episodes not found inside JSON structure.")
            
        return series_title, [ep['url'] for ep in ep_list]
        
    except Exception as e:
        raise Exception(f"Bypass Error: {str(e)}")
