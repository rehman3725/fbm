"""
Module loader for automatic blueprint registration.
Discovers and registers all blueprints from the blueprints directory.
"""

import importlib
import inspect
import logging
from pathlib import Path
from flask import Blueprint

logger = logging.getLogger(__name__)


def load_modules(app, blueprint_dir='blueprints'):
    """
    Automatically discover and register all blueprints from the blueprint directory.

    Args:
        app: Flask application instance
        blueprint_dir: Directory containing blueprint modules
    """
    blueprint_path = Path(blueprint_dir)
    if not blueprint_path.exists():
        return
    if getattr(app, '_modules_loaded', False):
        return

    modules = []
    for file in blueprint_path.glob('*.py'):
        if file.name.startswith('_'):
            continue
        modules.append((file.stem, file))

    for module_name, module_path in sorted(modules):
        try:
            spec = importlib.util.spec_from_file_location(f"{blueprint_dir}.{module_name}", module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            found = []
            for _, obj in inspect.getmembers(module):
                if isinstance(obj, Blueprint):
                    found.append(obj)

            if not found:
                logger.debug("No blueprints found in module '%s'", module_name)
                continue

            for blueprint in found:
                url_prefix = f"/{blueprint.name}"
                if hasattr(module, 'MODULE_CONFIG'):
                    config = module.MODULE_CONFIG
                    if 'url_prefix' in config:
                        url_prefix = config['url_prefix']

                if blueprint.name in app.blueprints:
                    logger.debug("Blueprint '%s' already registered, skipping duplicate.", blueprint.name)
                    continue

                app.register_blueprint(blueprint, url_prefix=url_prefix)
                logger.info("Registered blueprint '%s' from module '%s' at '%s'", blueprint.name, module_name, url_prefix)

        except Exception as e:
            logger.exception("Error loading module '%s': %s", module_name, e)

    app._modules_loaded = True


def get_modules_info(blueprint_dir='blueprints'):
    """
    Get information about all available modules.

    Returns:
        List of tuples containing (module_name, blueprints_in_module)
    """
    blueprint_path = Path(blueprint_dir)
    modules_info = []

    if not blueprint_path.exists():
        return modules_info

    for file in blueprint_path.glob('*.py'):
        if file.name.startswith('_'):
            continue
        module_name = file.stem

        try:
            spec = importlib.util.spec_from_file_location(f"{blueprint_dir}.{module_name}", file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            blueprints = []
            for _, obj in inspect.getmembers(module):
                if isinstance(obj, Blueprint):
                    blueprints.append(obj.name)

            if blueprints:
                modules_info.append((module_name, blueprints))
        except Exception:
            pass

    return modules_info
