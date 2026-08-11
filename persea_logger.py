# persea_logger.py
from ablock_logger import configure as configure_logcore

# source_project debe ser el GCP Project ID donde corre tu servicio
configure_logcore(service="todo-api", env="dev", source_project="seed-prod-b9508c89")