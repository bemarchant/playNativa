# Usa la imagen oficial de Python
FROM python:3.9-slim

# Establece el directorio de trabajo
WORKDIR /app

# Copia los archivos necesarios
COPY requirements.txt /app/

# Instala las dependencias
RUN pip install --no-cache-dir -r requirements.txt

# Copia el resto del código
COPY . /app/

# Expone el puerto en el que la app escuchará
EXPOSE 8000

# Comando para iniciar la aplicación con Gunicorn
CMD ["gunicorn", "playnativa_project.wsgi:application", "--bind", "0.0.0.0:8000"]
