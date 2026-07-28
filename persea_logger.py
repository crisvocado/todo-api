# Persea Agents Logger
# Add this import to your app's entry point (e.g., main.py or app.py):
#   import persea_logger

from ablock_logger import configure as configure_logcore

configure_logcore(service="todo-api")
