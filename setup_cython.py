"""
setup_cython.py — Сборка Cython-ядра PyCaesar (`engine._fast_core`).

Использование:
    python setup_cython.py
"""

import sys
from setuptools import setup, Extension
from Cython.Build import cythonize

if sys.platform == "win32":
    extra_compile_args = ["/O2"]
else:
    extra_compile_args = ["-O3", "-mpopcnt", "-Wno-unused-function"]

ext = Extension(
    name="engine._fast_core",
    sources=["recovered_src/engine/_fast_core.pyx"],
    extra_compile_args=extra_compile_args,
)

setup(
    name="PyCaesarCore",
    ext_modules=cythonize([ext], language_level=3),
    script_args=["build_ext", "--build-lib", "recovered_src"],
)
