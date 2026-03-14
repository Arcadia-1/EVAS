# Configuration file for the Sphinx documentation builder.
# Secondary language: English
import os
import sys

sys.path.insert(0, os.path.abspath(".."))

project = "EVAS"
author = "Zhishuai Zhang"
copyright = "2026, Zhishuai Zhang"
release = "0.1.0"
language = "en"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_autodoc_typehints",
    "myst_parser",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "furo"
html_theme_options = {
    "source_repository": "https://github.com/Arcadia-1/EVAS/",
    "source_branch": "main",
    "source_directory": "docs_en/",
    "announcement": (
        "📖 This page is in English. "
        "<a href='/'>切换到中文版</a>"
    ),
}
html_static_path = ["_static"]
html_css_files = ["custom.css"]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable", None),
}

autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
}

myst_enable_extensions = ["colon_fence", "deflist"]
