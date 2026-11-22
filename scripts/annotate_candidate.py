"""Render the candidate's physical graph over the source crop for review."""
from pathlib import Path
import json
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
m=json.loads((ROOT/"data/candidate_manifest.json").read_text(encoding="utf-8"))
src=Image.open(ROOT/"work/analyst/scheme_300.png").convert("RGB")
sx,sy=src.width/1296,src.height/1818
box=[40,1230,480,1515]
base=src.crop(tuple(round(v*(sx if i%2==0 else sy)) for i,v in enumerate(box)))
header=136
sidebar=650
canvas=Image.new("RGB",(base.width+sidebar,base.height+header),"#f8fafb")
canvas.paste(base,(0,header))
draw=ImageDraw.Draw(canvas)
font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf",23)
small=ImageFont.truetype("C:/Windows/Fonts/arial.ttf",21)
title=ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf",33)
draw.text((25,15),"Кандидат для согласования: верхняя часть деталировки УДР",font=title,fill="#14343f")
draw.text((25,61),"21 узел/порт · 20 внутренних ветвей · 2 источника · 11 клапанов. Все записи pending.",font=font,fill="#14343f")
draw.text((25,98),"Внутренний подсчёт минимумов и границы должен утвердить преподаватель. Jnn = UDR_Jnn; полные ID в JSON.",font=small,fill="#8c401b")

def pt(point):
    return ((point[0]-box[0])*sx,(point[1]-box[1])*sy+header)

layer=Image.new("RGBA",canvas.size,(0,0,0,0))
ld=ImageDraw.Draw(layer)
for pipe in m["pipes"]:
    ld.line([pt(p) for p in pipe["schematic_path"]],fill=(38,133,171,70),width=15)
canvas=Image.alpha_composite(canvas.convert("RGBA"),layer).convert("RGB")
draw=ImageDraw.Draw(canvas)
short={"SRC_K328":"S328","SRC_K331_AGZU1":"S331","BD_UPSV_TOP":"B_UPSV","BD_K417":"B417","BD_K426":"B426",
       "BD_R543":"B543","BD_K340_K335":"B340/335","BD_LOWER_16":"B16","BD_LOWER_18":"B18","BD_LOWER_19":"B19","BD_V06_END":"END6"}
for i in range(1,11):
    short[f"UDR_J{i:02}"]=f"J{i:02}"
offset={
"UDR_J01":(-90,-38),"UDR_J02":(7,27),"UDR_J03":(-24,-42),"UDR_J04":(-23,-42),"UDR_J05":(-23,-42),
"UDR_J06":(-14,25),"UDR_J07":(30,-35),"UDR_J08":(22,-26),"UDR_J09":(-52,-49),"UDR_J10":(23,15),
"SRC_K328":(-102,-34),"SRC_K331_AGZU1":(45,20),"BD_UPSV_TOP":(-96,35),"BD_K417":(-96,25),"BD_K426":(40,-42),
"BD_R543":(45,20),"BD_K340_K335":(-85,36),"BD_LOWER_16":(-120,-8),"BD_LOWER_18":(-92,-37),"BD_LOWER_19":(18,-42),"BD_V06_END":(-73,30)}
colors={"junction":"#147ba1","source":"#207b47","boundary":"#a66715"}
for node in m["nodes"]:
    x,y=pt(node["position"])
    dx,dy=offset[node["id"]]
    color=colors[node["kind"]]
    label=short[node["id"]]
    lx,ly=x+dx,y+dy
    bb=draw.textbbox((lx,ly),label,font=font)
    draw.line((x,y,lx+8,ly+12),fill=color,width=2)
    draw.rectangle((bb[0]-4,bb[1]-3,bb[2]+4,bb[3]+3),fill="white",outline=color,width=1)
    draw.text((lx,ly),label,font=font,fill=color)
    draw.ellipse((x-7,y-7,x+7,y+7),fill="white",outline=color,width=3)

# Mark the intentionally unconnected visual crossings/gaps as questions.
for b,label in [([267,1373,278,1387],"A4"),([197,1347,209,1425],"A5")]:
    p0,p1=pt(b[:2]),pt(b[2:])
    draw.rectangle((*p0,*p1),outline="#c25444",width=2)
    draw.text((p1[0]+5,p0[1]),label,font=small,fill="#a43729")

x=base.width+20
y=header+10
def line(text,color="#183944",gap=31):
    global y
    draw.text((x,y),text,font=small,fill=color)
    y+=gap

line("Синие точки: внутренние разветвления.")
line("Зелёные: источники S328, S331.")
line("Коричневые: условные порты границы.")
line("Голубая линия: физическая связь;")
line("она не означает разрешённый проток.")
line("A4: мостик; общего узла нет.","#a43729")
line("A5: разрыв №6—№17; связи нет.","#a43729")
line("B16/18/19: отсечение перед нижними клапанами.")
line("B340/335: один сборный ввод, не два.")
line("B_UPSV: верхний выход к УПСВ.")
y+=15
line("Ветви и привязанная арматура:",gap=38)
vmap={v["pipe_id"]:v for v in m["valves"]}
for pipe in m["pipes"]:
    text=f'{pipe["id"]}: {short[pipe["from_node"]]} — {short[pipe["to_node"]]}'
    if pipe["id"] in vmap:
        v=vmap[pipe["id"]]
        text+=f'  [№{int(v["id"].split("V")[-1])}, '+("открыт" if v["physical_state"]=="open" else "закрыт")+"]"
    line(text,gap=29)
y+=20
line("Длины/OD/стенка/ID: null; рабочий статус unknown.")
line("Белые зоны ниже/сбоку могут быть вне кандидата.")
line("Никакие данные не утверждены инженером.","#8c401b")
canvas.save(ROOT/"outputs/candidate_annotated.png")
print(str(ROOT/"outputs/candidate_annotated.png"))
