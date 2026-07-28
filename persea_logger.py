# persea_logger.py
from ablock_logger import configure as configure_logcore

configure_logcore(service="todo-api", env="dev", source_project="todo-api")
