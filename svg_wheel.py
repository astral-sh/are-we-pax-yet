import math
import xml.etree.ElementTree as et

HEADERS = b"""<?xml version=\"1.0\" standalone=\"no\"?>
<?xml-stylesheet href="wheel.css" type="text/css"?>
<!DOCTYPE svg PUBLIC \"-//W3C//DTD SVG 1.1//EN\"
\"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd\">
"""

PATH_TEMPLATE = """
M {start_outer_x},{start_outer_y}
A{outer_radius},{outer_radius} 0 0 1 {end_outer_x},{end_outer_y}
L {start_inner_x},{start_inner_y}
A{inner_radius},{inner_radius} 0 0 0 {end_inner_x},{end_inner_y}
Z
"""

FRACTION_LINE = 80
OFFSET = 20
PADDING = 10
OUTER_RADIUS = 180
INNER_RADIUS = OUTER_RADIUS / 2
CENTER = PADDING + OUTER_RADIUS


def annular_sector_path(start, stop):
    cos_stop = math.cos(stop)
    cos_start = math.cos(start)
    sin_stop = math.sin(stop)
    sin_start = math.sin(start)

    points = {
        "inner_radius": INNER_RADIUS,
        "outer_radius": OUTER_RADIUS,
        "start_outer_x": CENTER + OUTER_RADIUS * cos_start,
        "start_outer_y": CENTER + OUTER_RADIUS * sin_start,
        "end_outer_x": CENTER + OUTER_RADIUS * cos_stop,
        "end_outer_y": CENTER + OUTER_RADIUS * sin_stop,
        "start_inner_x": CENTER + INNER_RADIUS * cos_stop,
        "start_inner_y": CENTER + INNER_RADIUS * sin_stop,
        "end_inner_x": CENTER + INNER_RADIUS * cos_start,
        "end_inner_y": CENTER + INNER_RADIUS * sin_start,
    }
    return PATH_TEMPLATE.format(**points)


def add_annular_sectors(wheel, packages):
    # SVG arcs need distinct start and end points even for a full circle.
    sectors = packages * 2 if len(packages) == 1 else packages
    for index, result in enumerate(sectors):
        sector = et.SubElement(
            wheel,
            "path",
            d=annular_sector_path(*angles(index, len(sectors))),
            attrib={"class": result["css_class"]},
        )
        title = et.SubElement(sector, "title")
        title.text = f"{result['name']}: {result['title']}"


def angles(index, total):
    # Angle, in radians, of one wedge of the wheel.
    angle_per_wedge = math.tau / total
    # Used to turn the start of the wheel from east to north.
    quarter_circle = math.tau / 4

    # Angle of the beginning of the wedge.
    start = (index * angle_per_wedge) - quarter_circle
    # Angle of the end of the wedge.
    stop = start + angle_per_wedge

    return start, stop


def add_fraction(wheel, packages):
    text_attributes = {
        "class": "wheel-text",
        "text-anchor": "middle",
        "dominant-baseline": "central",
        "font-size": str(2 * OFFSET),
        "font-family": '"Helvetica Neue",Helvetica,Arial,sans-serif',
    }

    total = len(packages)
    pax_count = sum(package["css_class"] == "success" for package in packages)

    numerator = et.SubElement(
        wheel,
        "text",
        x=str(CENTER),
        y=str(CENTER - OFFSET),
        attrib=text_attributes,
    )
    numerator.text = str(pax_count)

    title = et.SubElement(numerator, "title")
    percentage = f"{pax_count / total:.0%} pax-compatible"
    title.text = percentage

    # Dividing line
    et.SubElement(
        wheel,
        "line",
        x1=str(CENTER - FRACTION_LINE // 2),
        y1=str(CENTER),
        x2=str(CENTER + FRACTION_LINE // 2),
        y2=str(CENTER),
        attrib={"class": "wheel-line", "stroke-width": "2"},
    )

    # Total packages
    total_packages = et.SubElement(
        wheel,
        "text",
        x=str(CENTER),
        y=str(CENTER + OFFSET),
        attrib=text_attributes,
    )
    total_packages.text = f"{total}"

    title = et.SubElement(total_packages, "title")
    title.text = percentage


def generate_svg_wheel(packages, destination):
    wheel = et.Element(
        "svg",
        viewBox=f"0 0 {2 * CENTER} {2 * CENTER}",
        version="1.1",
        xmlns="http://www.w3.org/2000/svg",
    )
    add_annular_sectors(wheel, packages)

    add_fraction(wheel, packages)

    destination.write_bytes(HEADERS + et.tostring(wheel, encoding="utf-8"))
