"""Reproducible transcription of a proposed UDR fragment; no source writes."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "GAP" / "Тех.схема НСС, НН Пальяновского мр 2025.pdf"
README = ROOT.parent / "GAP" / "README.txt"
REVIEW = dict(review_status="pending", reviewed_by=None, reviewed_at=None)


def ref(box, raw, fields, method="visual"):
    return dict(source_id="scheme_pdf", page=1, bbox=box, raw_text=raw,
                method=method, fields=fields)


def legend(fields):
    return dict(source_id="scheme_readme", line_start=4, line_end=4,
                raw_text="Степень открытия клапанов (прозрачный клапан - полностью открыт, закрашенный - полностью закрыт)",
                method="text_file", fields=fields)


def node(identifier, label, kind, point, context, reason=None, evidence=None):
    x, y = point
    refs = [ref([x-4, y-4, x+4, y+4],
                label if label != "Без подписи" else "Неподписанное разветвление/порт на деталировке УДР",
                ["position", "kind"], "visual")]
    if label == "Без подписи":
        refs.append(ref([x-4, y-4, x+4, y+4],
                        "Обозначение отсутствует; стабильный ID назначен при разметке",
                        ["original_label", "context"], "derived"))
    if evidence:
        refs.extend(evidence)
    return dict(id=identifier, original_label=label, kind=kind, context=context,
                position=point, boundary_reason=reason, source_refs=refs, **REVIEW)


nodes = [
    node("UDR_J01", "Без подписи", "junction", [103.44, 1316.16], "УДР: верхний коллектор, отвод К-417"),
    node("UDR_J02", "Без подписи", "junction", [110.4, 1316.16], "УДР: верхний коллектор, ввод К-426"),
    node("UDR_J03", "Без подписи", "junction", [161.28, 1316.16], "УДР: верхний коллектор, ответвление №5"),
    node("UDR_J04", "Без подписи", "junction", [203.04, 1316.16], "УДР: верхний коллектор, ответвление №6"),
    node("UDR_J05", "Без подписи", "junction", [240.0, 1316.16], "УДР: верхний коллектор, ответвление №7"),
    node("UDR_J06", "Без подписи", "junction", [259.44, 1316.16], "УДР: верхний коллектор, ввод К-328"),
    node("UDR_J07", "Без подписи", "junction", [272.4, 1316.16], "УДР: верхний коллектор, ответвление №8"),
    node("UDR_J08", "Без подписи", "junction", [161.28, 1408.56], "УДР: ввод К-340/К-335, ответвление №5/16"),
    node("UDR_J09", "Без подписи", "junction", [240.0, 1380.0], "УДР: ввод Р-543, ответвление №7/18"),
    node("UDR_J10", "Без подписи", "junction", [272.4, 1353.12], "УДР: ввод К-331 АГЗУ1, ответвление №8/19"),
    node("SRC_K328", "К - 328", "source", [259.44, 1263.36], "УДР: порт кустового ввода К-328",
         evidence=[ref([259,1262,286,1270], "К - 328", ["original_label", "kind"]),
                   ref([281,1075,365,1130], "К 328; К-328 - УДР", ["kind", "context"])]),
    node("SRC_K331_AGZU1", "К – 331 АГЗУ№1", "source", [406.56, 1341.6], "УДР: порт кустового ввода К-331 АГЗУ1",
         evidence=[ref([390,1328,443,1343], "К – 331 АГЗУ№1", ["original_label", "kind"]),
                   ref([531,1012,631,1145], "К -331; К-331 – УДР", ["kind", "context"])]),
    node("BD_UPSV_TOP", "УПСВ", "boundary", [69.84, 1316.16], "УДР: верхний выход к УПСВ",
         "Технологический объект УПСВ вне выбранного фрагмента; сохранён порт верхнего коллектора.",
         [ref([74,1305,94,1312], "УПСВ", ["original_label"])]),
    node("BD_K417", "К-417", "boundary", [69.6, 1392.0], "УДР: входная граница К-417",
         "В деталировке показан обрезанный ввод. Полный маршрут от К-417 и его параметры здесь не восстанавливаются.",
         [ref([73,1383,90,1392], "К-417", ["original_label"])]),
    node("BD_K426", "К - 426", "boundary", [110.4, 1256.4], "УДР: входная граница К-426",
         "Верхний вход деталировки обрезан; полный трубопровод К-426—УДР не входит в этот граф.",
         [ref([114,1257,137,1267], "К - 426", ["original_label"])]),
    node("BD_R543", "Р-543", "boundary", [406.56, 1380.0], "УДР: входная граница Р-543",
         "Показан порт ввода, а не полный трубопровод от скважины/точки врезки.",
         [ref([388,1372,407,1380], "Р-543", ["original_label"])]),
    node("BD_K340_K335", "К - 340, К - 335", "boundary", [68.64, 1408.56], "УДР: общий вход от К-340 и К-335",
         "Источник показывает один общий ввод. Разделение на отдельные кустовые потоки здесь отсутствует.",
         [ref([66,1410,112,1419], "К - 340, К - 335", ["original_label"])]),
    node("BD_LOWER_16", "Без подписи", "boundary", [161.28, 1421.76], "УДР: отсечение перед №16",
         "Условная граница фрагмента на непрерывной линии перед нижним клапаном №16; клапан и нижний коллектор вне графа."),
    node("BD_LOWER_18", "Без подписи", "boundary", [240.0, 1421.76], "УДР: отсечение перед №18",
         "Условная граница фрагмента перед нижним клапаном №18; боковая ветвь №23 требует отдельного согласования."),
    node("BD_LOWER_19", "Без подписи", "boundary", [272.4, 1421.76], "УДР: отсечение перед №19",
         "Условная граница фрагмента перед нижним клапаном №19; пересечение с Р-543 проходит без соединения."),
    node("BD_V06_END", "Без подписи", "boundary", [203.04, 1348.32], "УДР: видимый конец ответвления №6",
         "Ниже №6 линия отсутствует. Соединение с №17 не добавлено; требуется трактовка инженера."),
]
index = {n["id"]: n for n in nodes}


edge_rows = [
    ("P01", "BD_UPSV_TOP", "UDR_J01", None),
    ("P02", "UDR_J01", "UDR_J02", None),
    ("P03", "UDR_J02", "UDR_J03", None),
    ("P04", "UDR_J03", "UDR_J04", None),
    ("P05", "UDR_J04", "UDR_J05", None),
    ("P06", "UDR_J05", "UDR_J06", None),
    ("P07", "UDR_J06", "UDR_J07", None),
    ("P08", "BD_K417", "UDR_J01", [[69.6,1392],[103.44,1392],[103.44,1316.16]]),
    ("P09", "BD_K426", "UDR_J02", None),
    ("P10", "SRC_K328", "UDR_J06", None),
    ("P11", "SRC_K331_AGZU1", "UDR_J10", [[406.56,1341.6],[291.6,1341.6],[291.6,1353.12],[272.4,1353.12]]),
    ("P12", "BD_R543", "UDR_J09", None),
    ("P13", "BD_K340_K335", "UDR_J08", None),
    ("P14", "UDR_J03", "UDR_J08", None),
    ("P15", "UDR_J05", "UDR_J09", None),
    ("P16", "UDR_J07", "UDR_J10", None),
    ("P17", "UDR_J08", "BD_LOWER_16", None),
    ("P18", "UDR_J09", "BD_LOWER_18", None),
    ("P19", "UDR_J10", "BD_LOWER_19", None),
    ("P20", "UDR_J04", "BD_V06_END", None),
]
pipes = []
for identifier, left, right, path in edge_rows:
    points = path or [index[left]["position"], index[right]["position"]]
    xs, ys = zip(*points)
    bbox = [round(min(xs)-2,2),round(min(ys)-2,2),round(max(xs)+2,2),round(max(ys)+2,2)]
    pipes.append(dict(id=identifier, original_label="Без подписи", from_node=left, to_node=right,
        role="internal_connector", line_class="трубопровод без ВНП (чёрная линия по легенде)", active_state="unknown",
        parameters=dict(length_m=None,outer_diameter_mm=None,wall_thickness_mm=None,inner_diameter_mm=None),
        schematic_path=points,
        source_refs=[ref(bbox, "Непрерывный наблюдаемый участок деталировки УДР; собственного обозначения нет",
                         ["from_node","to_node","role","line_class"]),
                     ref(bbox, "ID назначен разметчиком, а не взят с исходной схемы", ["original_label"], "derived")], **REVIEW))

valve_rows = [
    ("01", "P03", 200, "25", "open", [116,1302,147,1325]),
    ("02", "P08", 250, "16", "open", [88,1342,137,1367]),
    ("03", "P10", 150, "16", "open", [222,1293,275,1314]),
    ("05", "P14", 100, "40", "closed", [134,1323,181,1353]),
    ("06", "P20", 100, "40", "closed", [172,1324,219,1350]),
    ("07", "P15", 150, "64", "open", [217,1324,258,1352]),
    ("08", "P16", 100, "40", "open", [251,1324,291,1353]),
    ("09", "P11", 100, "40", "open", [295,1325,344,1351]),
    ("10", "P09", 150, "16", "open", [77,1285,124,1310]),
    ("11", "P12", 200, "25", "open", [282,1370,344,1386]),
    ("14", "P13", 100, "40", "closed", [113,1390,151,1424]),
]
valves = []
for number, pipe_id, dn, rating, state, box in valve_rows:
    valves.append(dict(id="UDR_V"+number, original_label=f"№ {int(number)} {dn}х{rating}",
        pipe_id=pipe_id, physical_state=state, size_dn_mm=dn, pressure_rating_raw=rating,
        source_refs=[ref(box, f"№ {int(number)} {dn}х{rating}; " + ("прозрачный" if state == "open" else "закрашенный") + " символ",
                         ["original_label","physical_state","size_dn_mm","pressure_rating_raw"]),
                     legend(["physical_state"])], **REVIEW))


def ambiguity(identifier, kind, description, targets, refs):
    return dict(id=identifier, kind=kind, description=description, status="open", affected_ids=targets,
                source_refs=refs, resolution=None, reviewed_by=None, reviewed_at=None)


selection_id = "UDR_UPPER_CANDIDATE"
ambiguities = [
    ambiguity("A_MINIMA", "detail_mapping",
        "21 узел/порт и 20 ветвей посчитаны внутри деталировки УДР. Внутренние точки не являются 10 отдельными подписанными УЗ№. Преподаватель должен подтвердить допустимость такого фрагмента и такого подсчёта учебных минимумов.",
        [selection_id], [ref([45,1250,470,1466], "Схема УДР: внутренние разветвления и граничные порты", ["required_minima"], "derived")]),
    ambiguity("A_DETAIL_MAPPING", "detail_mapping",
        "Нужно подтвердить соответствие портов деталировки УДР основному виду. К-331 имеет подписи 330 м (2003) и 130 м (2022); их нельзя автоматически считать дублями или параллельными трубами. Длины внешних трасс не присвоены внутренним отрезкам деталировки.",
        [selection_id,"SRC_K328","SRC_K331_AGZU1","BD_K426","BD_K340_K335"],
        [ref([275,1010,642,1243], "Основной вид: К-328, К-331, УДР и подходящие линии", ["context"]),
         ref([45,1250,470,1466], "Схема УДР", ["context"])]),
    ambiguity("A_MISSING_GEOMETRY", "missing_parameter",
        "Все 20 внутренних отрезков имеют неизвестные длины и пока нераспределённые диаметры/толщины. Надписи 219х8 и 114х8 относятся к группам линий; распространение по каждому отрезку требует согласования. Ни длины из координат, ни нули, ни типовые значения не подставлены. Внутренний диаметр тоже null.",
        [p["id"] for p in pipes], [ref([66,1303,302,1466], "219х8; 114х8; без длин отдельных внутренних отрезков", ["parameters"])]),
    ambiguity("A_CROSSING_R543", "crossing",
        "Наблюдаемый мостик Р-543 через вертикаль К-331 трактуется как отсутствие соединения. P12 пересекает P19, отдельного общего узла нет. Требуется подтверждение трактовки.",
        ["P12","P19"], [ref([267,1373,278,1387], "Мостик в пересечении линии Р-543 и вертикальной линии К-331", ["from_node","to_node"])]),
    ambiguity("A_GAP_V06_V17", "connection",
        "Между №6 и №17 виден белый разрыв. В графе сохранён только видимый конец после №6, а связь с №17 и нижний участок не добавлены. Требуется пояснение, является ли разрыв физическим либо дефектом изображения.",
        [selection_id,"BD_V06_END","P20","UDR_V06"],
        [ref([197,1347,209,1425], "Отсутствующая линия между символами №6 и №17", ["from_node","to_node"])]),
    ambiguity("A_LOWER_SCOPE", "connection",
        "Условные границы поставлены перед №16/18/19. Боковая закрытая ветвь №23 100х40 на вертикали №7/18 не имеет прослеживаемого второго конца и исключена из связного графа. Нижний коллектор, №15, газовая ветвь и выход к ДНУ исключены. Нужно утвердить границы.",
        [selection_id,"BD_LOWER_16","BD_LOWER_18","BD_LOWER_19"],
        [ref([193,1393,243,1415], "№23 100х40: один видимый присоединённый конец", ["scope"]),
         ref([152,1418,279,1465], "№16/18/19 и нижний коллектор за границей кандидата", ["scope"])]),
    ambiguity("A_LINE_DATE", "line_status",
        "Название схемы относится к 2025 году, в основном виде имеются исторические отметки 2022—2024. Для внутренних отрезков не определён отдельный рабочий статус на согласованную дату; active_state=unknown. Цвет означает класс трубопровода, а не доказанный текущий рабочий статус.",
        [p["id"] for p in pipes], [ref([79,1515,235,1548], "Легенда типов линий", ["line_class","active_state"])]),
    ambiguity("A_VALVE_REVIEW", "valve_state",
        "Состояния наблюдаются по заливке символов и правилу README: прозрачный открыт, закрашенный закрыт. Нужно подтвердить актуальность состояний. Второй компонент маркировки клапана сохранён строкой без назначения единицы давления.",
        [v["id"] for v in valves], [legend(["physical_state"]), ref([45,1250,470,1466], "Схема УДР: символы и подписи арматуры", ["physical_state","pressure_rating_raw"])]),
]

manifest = dict(schema_version="1.0", case_id="palyanovo_udr_candidate_2025",
    sources=[dict(id="scheme_pdf", kind="pdf", path="../GAP/"+SOURCE.name,
                  sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                  pages=[dict(number=1,width_pt=1296,height_pt=1818)]),
             dict(id="scheme_readme",kind="text",path="../GAP/README.txt",
                  sha256=hashlib.sha256(README.read_bytes()).hexdigest(),pages=[])],
    selection=dict(id=selection_id, **REVIEW,
        regions=[ref([45,1250,470,1466], "Предложенный верхний фрагмент деталировки УДР", ["selection"]),
                 ref([275,1010,642,1243], "Основной вид для привязки портов деталировки", ["context"])],
        required_minima=dict(nodes=10,pipes=12,sources=2,valves=4),
        notes="Черновой кандидат, не утверждён преподавателем. 21 узел/порт (10 внутренних junction, 2 кустовых источника, 9 границ), 20 реальных непрерывно прослеживаемых внутренних ветвей, 11 клапанов. Подсчёт внутренних узлов/ветвей для учебных минимумов требует согласования. Обрезанные вводы не считаются полными трассами; все неизвестные параметры null. Граф физически связный; закрытая арматура ограничивает проток. Ни одна запись не подтверждена."),
    nodes=nodes,pipes=pipes,valves=valves,ambiguities=ambiguities)

if __name__ == "__main__":
    destination = ROOT / "data" / "candidate_manifest.json"
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"path":str(destination),"nodes":len(nodes),"pipes":len(pipes),"valves":len(valves),
                      "sources":sum(n["kind"]=="source" for n in nodes),"pdf_sha256":manifest["sources"][0]["sha256"]},ensure_ascii=False))
