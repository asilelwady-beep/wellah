#!/usr/bin/env python3
"""Non-destructive validation of a 20-column headerless supermarket XLSX.
No production database writes. Run before mapping source columns to products.
"""
import argparse, json, zipfile, xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
NS={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
def inspect(path):
    with zipfile.ZipFile(path) as z:
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            root=ET.fromstring(z.read('xl/sharedStrings.xml'))
            strings=[''.join(t.text or '' for t in si.findall('.//m:t',NS)) for si in root.findall('m:si',NS)]
        sheet=next(x for x in z.namelist() if x.startswith('xl/worksheets/sheet') and x.endswith('.xml'))
        root=ET.fromstring(z.read(sheet))
        counts=Counter(); missing=[]; samples=[]
        for row in root.findall('.//m:sheetData/m:row',NS):
            cells={}
            for cell in row.findall('m:c',NS):
                ref=cell.attrib.get('r','')
                letters=''.join(ch for ch in ref if ch.isalpha())
                index=0
                for ch in letters: index=index*26+ord(ch)-64
                value=cell.find('m:v',NS)
                inline=cell.find('m:is',NS)
                val=value.text if value is not None else ''.join(t.text or '' for t in inline.findall('.//m:t',NS)) if inline is not None else None
                if cell.attrib.get('t')=='s' and val is not None: val=strings[int(val)]
                cells[index]=val
            counts['rows']+=1
            if len(cells)>20: counts['rows_over_20_columns']+=1
            if not str(cells.get(2) or '').strip(): missing.append(int(row.attrib['r']))
            if len(samples)<3: samples.append([cells.get(i) for i in range(1,21)])
        return {'rows':counts['rows'],'columns_expected':20,'rows_over_20_columns':counts['rows_over_20_columns'],'missing_names_rows':missing[:100],'missing_names_count':len(missing),'sample_rows':samples,'category_target':'سوبر ماركت','import_performed':False}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('xlsx');p.add_argument('--report',default='supermarket-import-audit.json')
    a=p.parse_args(); result=inspect(Path(a.xlsx))
    Path(a.report).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='sample_rows'},ensure_ascii=False))
