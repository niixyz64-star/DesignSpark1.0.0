import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
import archive_store as store
import if_crawler as crawler

class Locator:
    first = property(lambda self:self)
    def wait_for(self,**kw): pass
    def inner_text(self): return '1 Projects'
    def evaluate_all(self,*args): return ['https://ifdesign.com/en/winner-ranking/project/example/123']
class Page:
    url='https://ifdesign.com/en/social-engagement/student-award/winner-overview/?yearId=1'
    def get_by_text(self,*args): return Locator()
    def locator(self,*args): return Locator()
class Client:
    page=Page()
    def get(self,url):
        return SimpleNamespace(text='<h1>Example</h1><span id="Award_Year">2025</span><button class="x--tag--x">03 Health</button><img alt="Example" src="/user-uploads/a.png">')

class ArchiveTests(unittest.TestCase):
    def test_if_incremental_and_missing_image_repair(self):
        with tempfile.TemporaryDirectory() as temp:
            args=crawler.build_parser().parse_args(['fetch','--year','2025','--output',temp])
            downloads=[]
            def download(client,url,folder,number,*args):
                downloads.append(url)
                path='image/001.png';Image.new('RGB',(3,3)).save(folder/path)
                return dict(url=url,status='downloaded',local_path=path)
            with patch.object(crawler,'select_list_filter'),patch.object(crawler,'require_selected_filter'),patch.object(crawler,'download_image',side_effect=download):
                crawler.collect(args,Client())
                card=next(Path(temp).rglob('卡片.md'))
                card.write_text(card.read_text(encoding='utf-8').split('<!-- user-notes -->')[0]+'<!-- user-notes -->KEEP',encoding='utf-8')
                crawler.collect(args,Client());self.assertEqual(len(downloads),1)
                (card.parent/'image/001.png').unlink()
                crawler.collect(args,Client());self.assertEqual(len(downloads),2)
                self.assertTrue(card.read_text(encoding='utf-8').endswith('KEEP'))
                self.assertEqual(len(list(Path(temp).rglob('卡片.md'))),1)
                self.assertFalse(list(Path(temp).rglob('metadata.json')))

    def test_frontmatter_delimiters_and_identity_move(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder=root/'old';folder.mkdir()
            record=dict(title='A --- B',source_id='1',source_url='https://example.org',description='a---b',images=[dict(local_path=None,status='pending')])
            store.write_card(folder,record)
            self.assertFalse(store.read_card(folder)['images_complete'])
            new=store.resolve_folder(root,root/'new','1')
            self.assertTrue((new/'卡片.md').exists())
            self.assertFalse(folder.exists())
            self.assertEqual(len(store.rebuild_index(root)),1)

if __name__=='__main__': unittest.main()
