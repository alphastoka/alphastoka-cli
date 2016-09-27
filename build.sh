#!/bin/bash
#
echo $1
docker build -t stoka-ig .
docker tag stoka-ig ssabpisa/stoka-ig:$1
docker push ssabpisa/stoka-ig:$1