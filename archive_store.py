"""DesignSpark card storage. JSON is valid YAML 1.2 frontmatter."""
import json
from pathlib import Path
from datetime import datetime

RECENT_YEARS = tuple(range(datetime.now().year-2, datetime.now().year+1))

def valid_image(folder, item):
    from PIL import Image
    if not item.get('local_path'): return False
    path = (folder / item['local_path']).resolve()
    if not item.get('local_path') or not path.is_relative_to(folder.resolve()) or not path.is_file():
        return False
    try:
        with Image.open(path) as image: image.verify()
        return item.get('status') == 'downloaded'
    except (OSError, ValueError): return False

def read_card(folder):
    path = folder / '卡片.md'
    if not path.exists(): return None
    text = path.read_text(encoding='utf-8')
    return json.loads(text.partition('\n')[2].partition('\n---\n')[0])

def write_card(folder, record):
    folder.mkdir(parents=True, exist_ok=True)
    path=folder/'卡片.md'
    marker='<!-- user-notes -->'
    notes='在这里记录你的观察。\n'
    if path.exists():
        previous=path.read_text(encoding='utf-8')
        if marker in previous: notes=previous.split(marker,1)[1]
    record['schema_version']='1.0'
    record['award_year']=record.get('award_year', record.get('year'))
    record['official_category']=record.get('category') or record.get('category_detail') or None
    record['collected_at']=record.get('fetched_at')
    record.setdefault('tags', [])
    record['images_complete']=bool(record.get('images')) and all(valid_image(folder,i) for i in record['images'])
    record['collection_status']='complete' if record['images_complete'] else 'incomplete'
    info=json.dumps(record,ensure_ascii=False,indent=2)
    pictures='\n\n'.join(f"![作品图片](<{i['local_path']}>)" if valid_image(folder,i) else f"图片未完成：{i.get('status')}" for i in record.get('images',[]))
    body=(f"# {record['title']}\n\n- 年份：{record.get('award_year',record.get('year'))}\n"
          f"- 类别：{record.get('category',record.get('category_detail','待分类'))}\n"
          f"- 奖项：{record.get('award_level',record.get('award','未提供'))}\n"
          f"- 来源：{record['source_url']}\n\n## 官方简介\n\n{record.get('description') or '未提供'}\n\n"
          f"## 作品图片\n\n{pictures or '未提供'}\n\n## 设计分析\n\n待按需补充；不代表官方结论。\n\n## 个人笔记\n\n{marker}{notes}")
    temp=path.with_suffix('.tmp');temp.write_text('---\n'+info+'\n---\n\n'+body,encoding='utf-8');temp.replace(path)

def rebuild_index(out):
    rows=[]
    for path in out.rglob('卡片.md'):
        row=read_card(path.parent)
        row['archive_path']=path.parent.relative_to(out).as_posix()
        rows.append(row)
    destination=out/'_index'/'index.json';destination.parent.mkdir(parents=True,exist_ok=True)
    temp=destination.with_suffix('.tmp');temp.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(destination)
    return rows


def resolve_folder(out, desired, source_id):
    """Keep one canonical directory per source/year/id when labels change."""
    existing=[]
    for path in out.rglob('卡片.md'):
        record=read_card(path.parent)
        if str(record.get('source_id')) == str(source_id): existing.append(path.parent)
    if len(existing)>1:
        raise ValueError(f'Duplicate source ID {source_id}: resolve existing copies before collecting')
    if existing and existing[0] != desired:
        if desired.exists():
            raise ValueError(f'Destination already exists: {desired}')
        desired.parent.mkdir(parents=True,exist_ok=True)
        existing[0].rename(desired)
    return desired
