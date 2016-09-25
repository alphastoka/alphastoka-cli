FROM python:3-onbuild
RUN pip install -r requirements.txt
CMD [ "python", "./instagram.py" ]