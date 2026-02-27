from .__about__ import __version__

import urllib.request
from html.parser import HTMLParser
import urllib.parse


class AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            attrs_dict = dict(attrs)
            self._current = {
                'href': attrs_dict.get('href', ''),
                'data-requires-python': attrs_dict.get('data-requires-python', ''),
                'data-dist-info-metadata': attrs_dict.get('data-dist-info-metadata', ''),
                'text': ''
            }

    def handle_data(self, data):
        if self._current is not None:
            self._current['text'] += data

    def handle_endtag(self, tag):
        if tag == 'a' and self._current is not None:
            self.links.append(self._current)
            self._current = None

pypi_simple = 'https://pypi.org/simple/'

url = pypi_simple + "liburlparser/"

req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})

try:
    with urllib.request.urlopen(req) as response:
        html = response.read().decode('utf-8')
except urllib.error.HTTPError as e:
    print(f"HTTP Error {e.code}: {e.reason}")
    exit(1)
except urllib.error.URLError as e:
    print(f"URL Error: {e.reason}")
    exit(1)

# print(html)
# exit(0)
parser = AnchorParser()
parser.feed(html)

for link in parser.links:
    # print(link)
    raw_href = link.get('href', '')
    parsed = urllib.parse.urlparse(raw_href)
    
    clean_url = urllib.parse.urlunparse(parsed._replace(fragment=''))
    fragment = parsed.fragment
    # continue
    print(f"Text: {link['text']}")
    print(f"  href:                    {raw_href}")
    print(f"  cleaned url:             {clean_url}")
    print(f"  data-requires-python:    {link.get('data-requires-python')}")
    # print(f"  data-core-metadata: {link.get('data-core-metadata')}")
    print(f"  data-dist-info-metadata: {link.get('data-dist-info-metadata')}")
    print(f"  fragment:                {fragment}")
    print(f"fragment == link.get('data-dist-info-metadata'): {link.get('data-dist-info-metadata') == fragment}")


    print()

