# candigv2-ingest
Ingest data into the CanDIGv2 stack

This repository assumes that you have a functional instance of CanDIGv2.

## CanDIGv2 components in use right now:
minio
htsget-server
chord-metadata
candig-server
federation-service
candig-data-portal
keycloak
tyk
opa
vault

## What you'll need:
* A valid user for CanDIGv2 that has site administration credentials.
* List of users that will have access to this dataset.
* Clinical data, saved as either an Excel file or as a set of csv files.
* Genomic data files in vcf format.
* File map of genomic files in a csv file, linking genomic sample IDs to the clinical samples.
* Reference genome used for the variant files.
* Manifest and mappings for clinical_ETL conversion.


ingest into:
* opa: set up permissions for dataset
* htsget: ingest DRS object
* katsu: clinical data and link to htsget
* candig-server: patientID, sampleID, vcf file for variant search


## Set environment variables:
* CANDIG_URL (same as TYK_LOGIN_TARGET_URL, if you're using CanDIGv2's example.env)
* KEYCLOAK_PUBLIC_URL
* CANDIG_CLIENT_ID
* CANDIG_CLIENT_SECRET
* CANDIG_SITE_ADMIN_USER
* CANDIG_SITE_ADMIN_PASSWORD

For convenience, you can update these in env.sh and run `source env.sh`.

## OPA
Create a new access.json file:
```bash
python opa_init.py --dataset <dataset> --userfile <user file> > access.json
```

If you're running OPA in the CanDIGv2 Docker stack, you should copy the file to the Docker volume to persist the change between restarts:
```bash
docker cp access.json candigv2_opa_1:/app/permissions_engine/access.json
``` 

## Htsget
### Genomic file preparation:
Files need to be in vcf or vcf.gz format.
* If .tbi file does not exist, create it
* Copy files to ECS

```bash
python htsget_ingest.py --dataset <dataset> --userfile <user file>
```


## Katsu
You'll need to generate a mapping file using the clinical_ETL tool, to translate your raw clinical data into an mcodepacket-compatible format:
```bash
python clinical_ETL/CSVConvert.py --input <directory of clinical csv files> --mapping <mapping manifest file>
```

Once you've generated the json ingest file, copy it to the katsu server, so that it is locally accessible:
```bash
docker cp input.json candigv2_chord-metadata_1:input.json
```

Then run the ingest tool:
```bash
python katsu_ingest.py --dataset $(DATASET) --input /input.json
```


## Candig-server
Candig-server needs its data ingested from the local command line; there is no REST API for ingest.

* You'll need to know the name of the referenceset on your candig-server instance. Make sure that the referenceset needed for your variant files is already created on your candig-server instance.
* You'll need a csv input file containing the mapping between the patient_ids and the variant_ids and the names of the columns corresponding to each. 
* The variant files need to be mounted on a path that is accessible to candig-server.

Run the candig_server_ingest.py script to generate a shell script and input json that you can copy and run on your candig-server instance.
```bash
python candig_server_ingest.py --dataset DATASET --input_file INPUT_FILE --patient_id PATIENT_ID_COL_NAME --variant_file_id VARIANT_ID_COL_NAME --path FILE_PATH --reference REFSET_NAME
```

This command will generate two files in a temp directory, `candigv1_data.json` and `candigv1_ingest.sh`. Copy these onto the candig_server instance and run `bash candigv1_ingest.sh`.

If you're running candig-server in the CanDIGv2 Docker stack:
```bash
docker cp temp/candigv1_data.json candigv2_candig-server_1:/app/candig-server/
docker cp temp/candigv1_ingest.sh candigv2_candig-server_1:/app/candig-server/
docker exec candigv2_candig-server_1 /app/candig-server/candigv1_ingest.sh
``` 


Run `make all` to install a synthetic project called `mohccn` and a dataset called `mcode-synthetic`.
Opa will allow the user specified in `$CANDIG_HOME/tmp/secrets/keycloak-test-user` to access the dataset.

After installation, you should be able to access the synthetic dataset:

* Get a user token, where the values for the data parameters are found in the files in tmp/secrets:

```
curl -X "POST" "http://auth.docker.localhost:8080/auth/realms/candig/protocol/openid-connect/token" \
     -H 'Content-Type: application/x-www-form-urlencoded; charset=utf-8' \
     --data-urlencode "client_id=local_candig" \
     --data-urlencode "client_secret=<value in $CANDIG_HOME/tmp/secrets/keycloak-client-local_candig-secret>" \
     --data-urlencode "grant_type=password" \
     --data-urlencode "username=<value in $CANDIG_HOME/tmp/secrets/keycloak-test-user>" \
     --data-urlencode "password=<value in $CANDIG_HOME/tmp/secrets/keycloak-test-user-password>" \
     --data-urlencode "scope=openid"
```

* You should see the dataset "mcode-synthetic" as part of the response for:

```
curl "http://docker.localhost:5080/katsu/api/datasets" \
     -H 'Authorization: Bearer <token>
``` 

* You should also be able to access all of the samples in the mcode-synthetic dataset via htsget if you're logged in as that user. If you're logged in as a different user (for example, the user specified in `$CANDIG_HOME/tmp/secrets/keycloak-test-user2`), you should get 403s. If you're not logged in at all, you'll get 401s.

```
curl "http://docker.localhost:3333/htsget/v1/variants/NA20787" \
     -H 'Authorization: Bearer <access_token>'
```
