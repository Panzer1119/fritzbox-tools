#!/bin/bash

#
# This bash script downloads the current Fritz Box event log as JSON file and as TXT file.
#
# The login procedure was implemented according to https://avm.de/fileadmin/user_upload/Global/Service/Schnittstellen/AVM_Technical_Note_-_Session_ID.pdf
#
# The script was tested successfully on MacOS High Sierra 10.13.6 with Fritz!Box 6490 Cable (kdg)

PASSWORD=<replace-with-your-fritz-box-password>

BASE_URL=http://fritz.box

CHALLENGE=$(curl ${BASE_URL}/login_sid.lua |grep -o -e "<Challenge>.*</Challenge>" |cut -d">" -f2 |cut -d"<" -f1)

MD5=$(echo -n ${CHALLENGE}-${PASSWORD} |iconv -t UTF-16LE |md5)

RESPONSE="${CHALLENGE}-${MD5}"

SID=$(curl -d "response=${RESPONSE}&lp=overview&username=" -H "Content-Type: application/x-www-form-urlencoded" POST ${BASE_URL}/index.lua |egrep -o -e "sid=[^&]+" |head -n 1 |cut -d"=" -f2)

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")

OUTPUT_FILE=log-${TIMESTAMP}
OUTPUT_FILE_JSON=json/${OUTPUT_FILE}.json
OUTPUT_FILE_TXT=txt/${OUTPUT_FILE}.txt

mkdir -p json txt

curl -d "xhr=1&lang=de&page=log&sid=${SID}" -H "Content-Type: application/x-www-form-urlencoded" POST ${BASE_URL}/data.lua > ${OUTPUT_FILE_JSON}

cat ${OUTPUT_FILE_JSON} |jq '.data.log[] | .[0] + " " + .[1] + " " + .[2]' > ${OUTPUT_FILE_TXT}

cat ${OUTPUT_FILE_TXT}

echo ${OUTPUT_FILE_TXT} 