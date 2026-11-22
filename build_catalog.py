"""Manually verified external pipeline annotations; independent of graph edges."""
from pathlib import Path
import csv
import hashlib
import json
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "GAP" / "Тех.схема НСС, НН Пальяновского мр 2025.pdf"
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)
FONT = "C:/Windows/Fonts/arial.ttf"
SMALL = ImageFont.truetype(FONT, 23)
HEAD = ImageFont.truetype(FONT, 28)
ROWS = [
    ("К-328", "УДР", 114,8,1810,2010,"К-328 - УДР, Ø114х8, L-1810м, 2010г.",[360,1138,413,1153],None,""),
    ("К-328", "т.вр.Р543",159,8,1510,2001,"К-328 – т.вр.Р543, Ø159х8, L-1510м, 2001г.",[243,1186,332,1201],None,"Отдельная подпись и другая трасса, чем К-328—УДР."),
    ("К-331", "УДР",114,8,330,2003,"К-331 – УДР, Ø114х8, L-330м, 2003г., продут декабрь 2022",[603,1063,659,1084],"K331_UDR","Не сливать с записью 130 м за 2022 год."),
    ("К-331", "УДР",114,8,130,2022,"К-331 – УДР, Ø114х8, L-130м, 2022г. Запуск в работу декабрь 2022",[419,973,508,988],"K331_UDR","Не сливать с записью 330 м за 2003 год."),
    ("К-426", "УДР УПСВ",159,8,3188,2017,"К-426 – УДР УПСВ Ø159х8 L-3188м, 2017г.(запуск 06.09.18)",[1056,1400,1136,1422],None,"Второй конец сохранён буквально как УДР УПСВ; точный порт требует подтверждения."),
    ("Скв. №1336", "УДР",159,5,270,2001,"Скв. №1336 – УДР, Ø159х5, L-270м, 2001г.",[603,1112,691,1127],None,""),
    ("К-335", "УЗ№12",114,8,700,2010,"К-335 – УЗ№12, Ø114х8, L-700м, 2010г.",[980,1373,1077,1384],None,""),
    ("Скв. №9322", "УЗ№12",159,8,750,2001,"Скв. №9322 – УЗ№12, Ø159х8, L-750м, 2001г.",[967,1268,1044,1284],None,""),
    ("УЗ№14", "УЗ№12",159,8,1600,2005,"УЗ№14 – УЗ№12, Ø159х8, L-1600м, 2005г.",[897,1439,964,1455],None,""),
    ("К-340", "УЗ№13",159,8,120,2004,"К-340 – УЗ№13, Ø159х8, L-120м, 2004г.",[910,1618,979,1635],"K340_UZ13","Не сливать с аналогичной подписью за 2022 год."),
    ("К-340", "УЗ№13",159,8,120,2022,"К-340 – УЗ№13, Ø159х8, L-120м, 2022г.",[712,1630,809,1640],"K340_UZ13","Не сливать с аналогичной подписью за 2004 год."),
    ("К-430", "Уз№20",159,8,4640,None,"К-430 – Уз№20, 159х8, L-4640м. Запуск 07.02.2022",[1046,378,1124,394],None,"2022 указан в дате запуска, не выдан за год строительства."),
    ("Уз№20", "Уз№8.1",159,8,193,None,"Уз№20-Уз№8.1, Ø159х8, L-193м. Запуск 07.02.2022 г.",[666,453,718,475],None,"Названия концов взяты из подписи; совмещение с узлами рисунка требует проверки."),
    ("Уз№8.1", "Уз№22",159,8,2052,None,"Уз№8.1-Уз№22, Ø159х8, L-2052м",[598,542,643,559],None,"Индекс Уз№22 не переименован автоматически в другой видимый УЗ."),
    ("Уз№22", "Уз№23",159,8,452,None,"Уз№22-Уз№23, Ø159х8, L-452м",[748,608,790,625],None,"Индексы концов не сопоставлены автоматически с иными обозначениями узлов."),
    ("Уз№23", "Уз№24",219,8,726,None,"Уз№23-Уз№24, Ø219х8, L-726м",[654,735,695,752],None,"Индексы концов не сопоставлены автоматически с иными обозначениями узлов."),
    ("Уз№24", "Уз№25",219,8,212,None,"Уз№24-Уз№25, Ø219х8, L-212м",[640,816,682,833],None,"Индексы концов не сопоставлены автоматически с иными обозначениями узлов."),
    ("Уз№25", "Уз№3.2",219,8,1666,None,"Уз№25-Уз№3.2, Ø219х8, L-1666м",[625,853,669,870],None,"Индексы концов не сопоставлены автоматически с иными обозначениями узлов."),
    ("Скв. №12364", "УЗ№13",159,8,120,2001,"Скв. №12364 – УЗ№13, Ø159х8, L-120м, 2001г.",[952,1527,1038,1544],None,""),
    ("Скв. №583", "К-340",89,8,250,2002,"Скв. №583 – К-340, 89х8, L-250м, 2002г.",[888,1642,972,1658],None,""),
]

records = []
for n, (start,end,od,wall,length,year,raw,box,group,note) in enumerate(ROWS,1):
    fields = ["from_label","to_label","outer_diameter_mm","wall_thickness_mm","length_m"]
    if year is not None:
        fields.append("year_as_printed")
    refs = [dict(source_id="scheme_pdf",page=1,bbox=box,raw_text=raw,method="text_layer",fields=fields),
            dict(source_id="scheme_pdf",page=1,bbox=box,raw_text="Подпись сверена по изображению PDF",method="visual",fields=fields),
            dict(source_id="scheme_pdf",page=1,bbox=box,raw_text=f"ID = OD - 2 × wall = {od} - 2 × {wall} = {od-2*wall} мм",method="derived",fields=["inner_diameter_mm"])]
    records.append(dict(id=f"EXT_OBS_{n:02}", from_label=start,to_label=end,raw_text=raw,
        parameters=dict(length_m=length,outer_diameter_mm=od,wall_thickness_mm=wall,inner_diameter_mm=od-2*wall),
        units=dict(length_m="m",outer_diameter_mm="mm",wall_thickness_mm="mm",inner_diameter_mm="mm"),
        year_as_printed=year,duplicate_group=group,source_refs=refs,
        association_status="unmapped_to_candidate",review_status="pending",reviewed_by=None,reviewed_at=None,
        notes=note))

catalog = dict(schema_version="1.0",kind="external_pipeline_annotation_catalog",case_id="palyanovo_udr_candidate_2025",
    warning="Независимый реестр читаемых подписей, не граф и не экспорт GAP. Ни одна запись не присвоена P01—P20. Концы сохранены по тексту; они не определяют автоматически топологию, направление потока или соответствие портам GAP. Все данные pending.",
    source=dict(id="scheme_pdf",path="../GAP/"+SOURCE.name,sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),page=1,width_pt=1296,height_pt=1818),
    ambiguities=["Две подписи К-331—УДР (330 м/2003 и 130 м/2022) сохранены отдельно: природа различия неизвестна.",
                 "Две подписи К-340—УЗ№13 (2004/2022) сохранены отдельно, несмотря на совпадение размеров и длины.",
                 "Названия Уз№22—Уз№25 в текстовых подписях не сопоставлены автоматически с другими индексами узлов на рисунке.",
                 "УДР УПСВ у ввода К-426 сохранено буквально, точная граница/порт не установлены.",
                 "Производный внутренний диаметр вычислен из наблюдаемых OD/толщины; это не отдельное измерение и не инженерное подтверждение."],
    records=records)

(ROOT/"data"/"pipeline_catalog.json").write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
with (OUT/"pipeline_catalog.csv").open("w",encoding="utf-8-sig",newline="") as stream:
    names=["id","from_label","to_label","length_m","outer_diameter_mm","wall_thickness_mm","inner_diameter_mm","year_as_printed","review_status","association_status","duplicate_group","source_id","page","bbox_pt","raw_text","notes"]
    writer=csv.DictWriter(stream,fieldnames=names)
    writer.writeheader()
    for row in records:
        writer.writerow({"id":row["id"],"from_label":row["from_label"],"to_label":row["to_label"],**row["parameters"],
            "year_as_printed":row["year_as_printed"],"review_status":row["review_status"],"association_status":row["association_status"],
            "duplicate_group":row["duplicate_group"],"source_id":"scheme_pdf","page":1,"bbox_pt":json.dumps(row["source_refs"][0]["bbox"]),
            "raw_text":row["raw_text"],"notes":row["notes"]})

image=Image.open(ROOT/"work"/"analyst"/"scheme_300.png").convert("RGB")
sx,sy=image.width/1296,image.height/1818
sheet=Image.new("RGB",(2100,1850),"white")
draw=ImageDraw.Draw(sheet)
draw.text((24,15),"Внешние подписи: визуальная сверка, 20 наблюдений (все pending)",font=HEAD,fill="#133947")
for i,row in enumerate(records):
    x=24+(i%2)*1040; y=65+(i//2)*177
    b=row["source_refs"][0]["bbox"]
    crop=image.crop((round(b[0]*sx),round(b[1]*sy),round(b[2]*sx),round(b[3]*sy)))
    scale=min(950/crop.width,100/crop.height)
    crop=crop.resize((round(crop.width*scale),round(crop.height*scale)),Image.Resampling.LANCZOS)
    draw.text((x,y),f'{row["id"]}: {row["from_label"]} — {row["to_label"]}',font=SMALL,fill="#133947")
    sheet.paste(crop,(x,y+35))
    draw.rectangle((x-4,y+31,x+crop.width+4,y+39+crop.height),outline="#92abb3",width=1)
sheet.save(ROOT/"work"/"analyst"/"catalog_contact_sheet.png")
print(json.dumps({"records":len(records),"json":str(ROOT/"data"/"pipeline_catalog.json"),"csv":str(OUT/"pipeline_catalog.csv")},ensure_ascii=False))
