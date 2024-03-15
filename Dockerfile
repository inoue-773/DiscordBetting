# Use the official Python image as the base image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install the required Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot script into the container
COPY bot.py .

# Copy the .env file into the container
COPY .env .

# Expose the port that the bot will run on (if needed)
# EXPOSE 8000

# Set the environment variables
ENV DISCORD_TOKEN=$DISCORD_TOKEN
ENV MONGODB_URI=$MONGODB_URI

# Run the bot script when the container starts
CMD ["python", "bot.py"]
