FROM python:3.11-slim

WORKDIR /app

# Копируем файлы проекта в контейнер
COPY . .

# Устанавливаем зависимости через pip
RUN pip install --no-cache-dir -r requirements.txt

# Запускаем бота
CMD ["python", "bot.py"]
