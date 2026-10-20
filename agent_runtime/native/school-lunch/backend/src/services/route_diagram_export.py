"""Excel DrawingML lines and text boxes stay editable; only the supplied map is an image."""
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree import ElementTree as ET
from openpyxl import Workbook
from openpyxl.drawing.image import Image
from openpyxl.styles import Font, Alignment
from openpyxl.worksheet.page import PageMargins
from src.services import route_diagram_service as service
from src.models.route_diagram import KitchenLayout
from PIL import Image as PILImage

XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
EMU = 9525


def child(parent, ns, tag, **attrs):
    return ET.SubElement(parent, f"{{{ns}}}{tag}", {k: str(v) for k, v in attrs.items()})


def shape(root, ident, name, x, y, width, height):
    # Absolute EMU positions use the image's pixel coordinate system (A3 starts
    # after two explicitly sized 15pt rows). Cell widths vary by renderer, and
    # oversized cell offsets are clamped by LibreOffice; neither is a pixel grid.
    anchor = child(root, XDR, "absoluteAnchor")
    child(anchor, XDR, "pos", x=round(x*EMU), y=round(y*EMU))
    child(anchor, XDR, "ext", cx=max(1, round(width * EMU)), cy=max(1, round(height * EMU)))
    sp = child(anchor, XDR, "sp")
    nv = child(sp, XDR, "nvSpPr")
    child(nv, XDR, "cNvPr", id=ident, name=name)
    child(nv, XDR, "cNvSpPr")
    props = child(sp, XDR, "spPr")
    transform = child(props, A, "xfrm")
    child(transform, A, "off", x=round(x * EMU), y=round(y * EMU))
    child(transform, A, "ext", cx=max(1, round(width * EMU)), cy=max(1, round(height * EMU)))
    child(anchor, XDR, "clientData")
    return sp, props


def add_shapes(root, routes, layout, width, height):
    ident = 2
    for route in routes:
        pts = [(p[0]*width, p[1]*height) for p in service.points(route, layout)]
        for i, (a, b) in enumerate(zip(pts, pts[1:])):
            if a == b:
                # Repeated work at one machine is not a transport movement.
                continue
            style = service.appearance(route, i)
            left, top = min(a[0], b[0]), min(a[1], b[1])
            dx, dy = abs(a[0]-b[0]), abs(a[1]-b[1])
            _, props = shape(root, ident, f"経路 {route['id']}-{i+1}", left, top+40, dx, dy)
            ident += 1
            geom = child(props, A, "custGeom")
            for tag in ("avLst", "gdLst", "ahLst", "cxnLst"):
                child(geom, A, tag)
            child(geom, A, "rect", l=0, t=0, r="r", b="b")
            path = child(child(geom, A, "pathLst"), A, "path", w=max(1, round(dx*EMU)), h=max(1, round(dy*EMU)), fill="none")
            for tag, p in (("moveTo", a), ("lnTo", b)):
                child(child(path, A, tag), A, "pt", x=round((p[0]-left)*EMU), y=round((p[1]-top)*EMU))
            child(props, A, "noFill")
            line = child(props, A, "ln", w=19050)
            child(child(line, A, "solidFill"), A, "srgbClr", val=style["color"][1:])
            child(line, A, "prstDash", val="dash" if style["line_style"] == "dashed" else "solid")
            child(line, A, "tailEnd", type="triangle", w="sm", len="sm")
        label = route.get("label_position")
        # Unpositioned labels belong in the complete legend. Placing all of
        # them at a shared first waypoint produces unreadable overlapping text.
        if label is None:
            continue
        x, y = label[0]*width, label[1]*height
        # Keep the entire editable label box inside the printable map, even at (1,1).
        short_label = route.get("map_label")
        label_width = min(12+len(short_label)*12, width) if short_label else min(300, width)
        label_height = min(20 if short_label else 52, height)
        x, y = min(x, width-label_width), min(y, height-label_height)
        sp, props = shape(root, ident, f"食材名 {route['id']}", x, y+40, label_width, label_height)
        ident += 1
        child(child(props, A, "prstGeom", prst="rect"), A, "avLst")
        child(props, A, "noFill")
        child(child(props, A, "ln"), A, "noFill")
        body = child(sp, XDR, "txBody")
        body_props = child(body, A, "bodyPr", wrap="square", lIns=0, rIns=0, tIns=0, bIns=0)
        child(body_props, A, "normAutofit")
        child(body, A, "lstStyle")
        run = child(child(body, A, "p"), A, "r")
        run_props = child(run, A, "rPr", lang="ja-JP", sz=1100)
        child(child(run_props, A, "solidFill"), A, "srgbClr", val=service.appearance(route, 0)["color"][1:])
        child(run_props, A, "ea", typeface="Yu Gothic")
        child(run, A, "t").text = route.get("map_label") or route["label"]


def export_editable(session, diagrams, layouts=None):
    if layouts is None:
        from src.services.route_export_snapshot import begin_export_read, freeze_row
        begin_export_read(session)
        layouts = {row.id: freeze_row(row) for row in session.query(KitchenLayout).filter(KitchenLayout.id.in_({diagram.layout_id for diagram in diagrams})).populate_existing().all()}
        diagrams = [freeze_row(diagram) for diagram in diagrams]
    wb = Workbook()
    wb.remove(wb.active)
    drawing_data = {}
    for index, diagram in enumerate(diagrams, 1):
        layout = layouts[diagram.layout_id]
        with PILImage.open(BytesIO(layout.image)) as original:
            scale = min(900/original.width, 560/original.height)
            width, height = original.width*scale, original.height*scale
        ws = wb.create_sheet(diagram.service_date.isoformat())
        ws.sheet_view.showGridLines = False
        ws["A1"] = f"作業動線図  {diagram.service_date}  版 {diagram.version}"
        ws.merge_cells("A1:R1")
        for col in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            ws.column_dimensions[col].width = 7
        for row in range(1, 36+len(diagram.routes)):
            ws.row_dimensions[row].height = 15
        image = Image(BytesIO(layout.image))
        image.width, image.height = width, height
        ws.add_image(image, "A3")
        # Keep the 200-character layout name away from the fixed A3 map anchor.
        row = int((height+40)/20)+2
        ws.cell(row, 1, layout.name)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=17)
        ws.cell(row, 1).alignment = Alignment(wrap_text=True, vertical="top")
        # Column R belongs to legend keys, including beside the map footer.
        lines = sum(max(1, (len(line)+51)//52) for line in layout.name.split("\n"))
        ws.row_dimensions[row].height = max(18, lines*18)
        row += 1
        # Keep the complete legend next to the map, not on pages after it.
        # Its cells do not intersect A:R, so wrapping cannot move the image or
        # alter the shared pixel coordinate system used by the editable lines.
        map_last_row = row
        row = 3
        legend_rows = {}
        for route in diagram.routes:
            label = route["label"]
            if route.get("map_label"):
                ws.cell(row, 18, route["map_label"])
                ws.cell(row, 18).alignment = Alignment(wrap_text=True, vertical="top")
            ws.cell(row, 19, label)
            ws.merge_cells(start_row=row, start_column=19, end_row=row, end_column=26)
            ws.cell(row, 19).alignment = Alignment(wrap_text=True, vertical="top")
            # Fixed 15pt rows under the map are a geometric invariant. A long
            # legend entry uses multiple vertically merged rows instead.
            lines = sum(max(1, (len(line)+27)//28) for line in label.split("\n"))
            if lines > 1:
                ws.unmerge_cells(start_row=row, start_column=19, end_row=row, end_column=26)
                ws.merge_cells(start_row=row, start_column=19, end_row=row+lines-1, end_column=26)
            legend_rows[row] = service.appearance(route, 0)["color"][1:]
            row += lines
        for cells in ws:
            for cell in cells:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                    cell.font = Font(name="Yu Gothic", size=10 if cell.column >= 19 else 11,
                                     color=legend_rows.get(cell.row, "000000") if cell.column >= 18 else "000000")
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A3
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.print_title_rows = "1:1"
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_margins = PageMargins(left=.2, right=.2, top=.2, bottom=.2, header=0, footer=0)
        ws.print_area = f"A1:Z{max(31, map_last_row, row-1)}"
        drawing_data[f"xl/drawings/drawing{index}.xml"] = (diagram.routes, layout, width, height)
    output = BytesIO()
    wb.save(output)
    result = BytesIO()
    with ZipFile(output) as source, ZipFile(result, "w", ZIP_DEFLATED) as target:
        for entry in source.infolist():
            content = source.read(entry.filename)
            if entry.filename.startswith("xl/drawings/_rels/"):
                root = ET.fromstring(content)
                for rel in root:
                    value = rel.get("Target", "")
                    if value.startswith("/xl/media/"):
                        rel.set("Target", "../media/"+value.removeprefix("/xl/media/"))
                content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            if entry.filename in drawing_data:
                root = ET.fromstring(content)
                add_shapes(root, *drawing_data[entry.filename])
                content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(entry, content)
    result.seek(0)
    return result
