#!/bin/bash

#
# Forked from https://gist.github.com/BigNerd/b3d79fd1b40b5667a5663eaa5fa9e80f
#
# This bash script downloads the current Fritz!Box event log as JSON file, a TXT file,
# and appends the new entries to a combined TXT file.
#
# The script was tested successfully on MacOS Sequoia 15.3 with Fritz!Box 6690 AX V7.58
# 'jq' was installed via Homebrew
#
# The login procedure was implemented according to 
#  German - https://avm.de/fileadmin/user_upload/Global/Service/Schnittstellen/AVM_Technical_Note_-_Session_ID.pdf
#  English - https://avm.de/fileadmin/user_upload/Global/Service/Schnittstellen/Session-ID_english_13Nov18.pdf
#   or https://avm.de/fileadmin/user_upload/Global/Service/Schnittstellen/Session-ID_deutsch_13Nov18.pdf
#
# The selected Fritz!Box user needs the permissions to view/edit the configuration.
# To get the user name you can select "use username and password" on the Fritz!Box login page, then you
# can see the user name in the combo box. The best way is to create a new user for this script with
# a strong password, only the neccessary permissions, and no access from the internet.
#

# uncomment the next line for script debugging
#set -vx

# output files are relative to where the script is
#
WORKING_DIR="$(dirname "$(realpath "$0")")"
#
# alternatively, uncomment the line below and set it to the directory of your choosing
#WORKING_DIR=<choosen directory>

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
OUTPUT_FILE=log-${TIMESTAMP}
JSON_PATH=$WORKING_DIR/json
TXT_PATH=$WORKING_DIR/txt
OUTPUT_FILE_JSON=$JSON_PATH/$OUTPUT_FILE.json
OUTPUT_FILE_TXT=$TXT_PATH/$OUTPUT_FILE.txt
OUTPUT_FILE_TXT_COMBINED=$TXT_PATH/log-combined
COMBINED_FILE_MAX_SIZE=5242880  #5 mb in bytes

# Store the Fritz!Box username and password in a file (for a bit better security)
#
# If the username and password are not set in this script and no credentials file exists,
# this script prompts you for them.
#
# Create a file called "credentials" in the WORKING_DIR with the following format
#
# username password
#
# Alternatively, uncomment the below lines and set them directly in this script
#
#FRITZ_USERNAME="<replace-with-your-fritz-box-username>"
#FRITZ_PASSWORD="<replace-with-your-fritz-box-password>"
#
if [[ -z "${FRITZ_USERNAME}" || -z "${FRITZ_PASSWORD}" ]]
then
  CREDENTIALS_FILE=${WORKING_DIR}/credentials
  if [ ! -f "${CREDENTIALS_FILE}" ]
      then
        # No credentials file was found, prompt for username and password 
        read FRITZ_USERNAME
       if [ -z "${FRITZ_USERNAME}" ]; then
         echo "You did not enter a username, exiting"
	 exit 1
       fi
       read -s FRITZ_PASSWORD
       if [ -z "${FRITZ_PASSWORD}" ]; then
         echo "You did not enter a password, exiting"
         exit 1
       fi
     else
	FRITZ_USERNAME=$( awk '{print $1}' ${CREDENTIALS_FILE} )
	FRITZ_PASSWORD=$( awk '{print $2}' ${CREDENTIALS_FILE} )
  fi
fi

BASEURL="http://fritz.box"

set -e -o pipefail
if ! which >/dev/null 2>&1 iconv ; then
  echo 1>&2 "Error: 'iconv' is not installed"
  exit 1
fi
if ! which >/dev/null 2>&1 curl ; then
  echo 1>&2 "Error: 'curl' is not installed"
  exit 1
fi

# get current session id and challenge
resp=$(curl -s "$BASEURL/login_sid.lua")

if [[ "$resp" =~ \<SID\>(0+)\</SID\> ]] ; then
  # SID=0 => not logged in
  if [[ "$resp" =~ \<BlockTime\>([0-9a-fA-F]+)\</BlockTime\> ]] ; then
    BLOCKTIME="${BASH_REMATCH[1]}"
    if [[ "${BLOCKTIME}" -gt "0" ]] ; then
      echo 1>&2 "BlockTime=${BLOCKTIME}, sleeping until unblocked"
      sleep $(( ${BLOCKTIME} + 1 ))
    fi
  fi
  if [[ "$resp" =~ \<Challenge\>([0-9a-fA-F]+)\</Challenge\> ]] ; then
    CHALLENGE="${BASH_REMATCH[1]}"
    
    # replace all Unicode codepoints >255 by '.' because of a bug in the Fritz!Box.
    # Newer Fritz!Box OS versions don't allow to enter such characters.
    # This requires that the locale environment is setup to UTF8, which is the default on MacOS Sequoia 
    FRITZ_PASSWORD=$(echo "${FRITZ_PASSWORD}" | sed 0's/[\u0100-\U0010ffff]/./g')
        
    if which >/dev/null 2>&1 md5 ; then
      MD5=$(echo -n "${CHALLENGE}-${FRITZ_PASSWORD}" | iconv --from-code=UTF-8 --to-code=UTF-16LE | md5 )
    elif which >/dev/null 2>&1 md5sum ; then
      MD5=$(echo -n "${CHALLENGE}-${FRITZ_PASSWORD}" | iconv --from-code=UTF-8 --to-code UTF-16LE | md5sum | cut -f1 -d ' ')
    else
      echo 1>&2 "Error: neither 'md5' nor 'md5sum' are installed"
      exit 1
    fi
    RESPONSE="${CHALLENGE}-${MD5}"
    resp=$(curl -s -G -d "response=${RESPONSE}" -d "username=${FRITZ_USERNAME}" "${BASEURL}/login_sid.lua")
  fi
fi

if ! [[ "$resp" =~ \<SID\>(0+)\</SID\> ]] && [[ "$resp" =~ \<SID\>([0-9a-fA-F]+)\</SID\> ]] ; then
  # either SID was already non-zero (authentication disabled) or login succeeded
  SID="${BASH_REMATCH[1]}"
  #echo 1>&2 "SessionID=$SID"
  
  mkdir -p ${JSON_PATH} ${TXT_PATH}
  curl -s -d "xhr=1&lang=de&page=log&sid=${SID}" -H "Content-Type: application/x-www-form-urlencoded" "${BASEURL}/data.lua" > "${OUTPUT_FILE_JSON}"

  # check if the output file was successfully created
  if [ ! -f "${OUTPUT_FILE_JSON}" ]; then
    echo 1>&2 "Error: The output file ${OUTPUT_FILE_JSON} was not created"
    exit 1
  fi

  if which >/dev/null 2>&1 jq ; then
    jq -r '.data.log[] |
       .date + " " + .time + " " + .group + " " + ( .id | tostring) + " " + .msg'  < "${OUTPUT_FILE_JSON}" > "${OUTPUT_FILE_TXT}"

    # Create a combined output text file by appending the latest entries
    # 
    # check if the combined file exists and create a new one if it is over the max size
    #
    if compgen -G "${TXT_PATH}/log-combined*" > /dev/null 
    then
      COMBINED_FILE_CURRENT=$( cd ${TXT_PATH} && ls -t log-combined* | head -n1 && cd ${WORKING_DIR} )
      COMBINED_FILE_CURRENT_SIZE=$( stat -f%z ${TXT_PATH}/${COMBINED_FILE_CURRENT} )
    fi

    if [[ -z "${COMBINED_FILE_CURRENT}" || "${COMBINED_FILE_CURRENT_SIZE}" -gt "${COMBINED_FILE_MAX_SIZE}" ]]
    then
	OUTPUT_FILE_TXT_COMBINED=${OUTPUT_FILE_TXT_COMBINED}-${TIMESTAMP}.txt
    else
	OUTPUT_FILE_TXT_COMBINED=${TXT_PATH}/${COMBINED_FILE_CURRENT}
    fi

    # get the last entry timestamp in the latest json file
    #
    LAST_FILE=$( ls -t $JSON_PATH/log-* | head -n2 | tail -n1 )

    if [ -z "$LAST_FILE" ]
    then
      # No file was found, set LE_TIMESTAMP to unix epoch
      LE_TIMESTAMP=$( date -j -f %s "+%Y-%m-%dT%H:%M:%S" 0 )
    else
      LE_TIMESTAMP="$( jq -r '.data.log[0] | .date + "T" + .time' < $LAST_FILE )"
      LE_TIMESTAMP=$(echo $LE_TIMESTAMP | sed 's/\([0-9]*\)\.\([0-9]*\)\.\([0-9]*\)T\(.*\)/20\3-\2-\1T\4/') 
    fi

    # append the new entries to the combined log text file
    #
    jq -r --arg LE_TIMESTAMP "$LE_TIMESTAMP" '[.data.log[] |
       {timestamp: ((.date + "T" + .time) |
       sub("(?<a>[0-9]*).(?<b>[0-9]*).(?<c>[0-9]*)T(?<d>.*)"; "20" + .c + "-" + .b + "-" + .a + "T" + .d)),
       group: .group, id: (.id | tostring), msg: .msg}] |
       sort_by(.timestamp) |
       map(select(.timestamp > $LE_TIMESTAMP)) | .[] |
       .timestamp + " " + .group + " " + .id + " " + .msg' < "${OUTPUT_FILE_JSON}"  >> "${OUTPUT_FILE_TXT_COMBINED}"

    #cat ${OUTPUT_FILE_TXT}
    echo ${OUTPUT_FILE_JSON}
    echo ${OUTPUT_FILE_TXT}
    echo ${OUTPUT_FILE_TXT_COMBINED}
  else
    echo 1>&2 "Warning: 'jq' is not installed, cannot create text version of logfile"
    echo ${OUTPUT_FILE_JSON}
  fi

else
  echo 1>&2 "ERROR: login failed, response: $resp"
  exit 1
fi

# vim: set ts=2 sw=2 expandtab: