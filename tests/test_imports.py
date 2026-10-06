import importlib
import pkgutil

import festify


def test_every_module_imports():
    for m in pkgutil.iter_modules(festify.__path__):
        importlib.import_module(f"festify.{m.name}")
