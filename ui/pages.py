"""三个主要工作台入口。"""

def render_analysis() -> None:
    from ui.import_page import render_import_page

    render_import_page()


def render_comparison() -> None:
    from ui.comparison import render_comparison_page
    render_comparison_page()


def render_report() -> None:
    from ui.report_page import render_report_page
    render_report_page()
