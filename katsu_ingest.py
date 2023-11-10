import json
import os
import sys
import traceback
import argparse
from http import HTTPStatus

import requests

import auth
from ingest_result import IngestPermissionsException

sys.path.append("clinical_ETL_code")
from clinical_ETL_code.mohschema import MoHSchema

CANDIG_URL = os.environ.get("CANDIG_URL")

def update_headers(headers):
    """
    For new auth model
    refresh_token = headers["refresh_token"]
    bearer = auth.get_bearer_from_refresh(refresh_token)
    new_refresh = auth.get_refresh_token(refresh_token=refresh_token)
    headers["refresh_token"] = new_refresh
    headers["Authorization"] = f"Bearer {bearer}"
    """
    pass


def read_json(file_path):
    """Read data from either a URL or a local file in JSON format.

    Parameters
    ----------
    file_path : str
        The URL or the local file path from which the data should be read.

    Returns
    -------
    data : dict or None
        A dictionary containing the data read from the URL or local file,
        or `None` if the data could not be retrieved or the file does not exist.

    Examples
    --------
    >>> read_json("https://example.com/remote_file.json")
    >>> read_json("data/local_file.json")
    """

    if file_path.startswith("http"):
        try:
            response = requests.get(file_path)
            response.raise_for_status()
            data = response.json()
            return data
        except requests.exceptions.RequestException as e:
            print("Failed to retrieve data. Error:", e)
            return None
    else:
        try:
            with open(file_path, "r") as f:
                data = json.load(f)
                return data
        except FileNotFoundError as e:
            print("File not found. Error:", e)
            return None


def ingest_flattened(fields, headers):
    result = {
        "errors": [],
        "results": []
    }
    name_mappings = {
        "radiation": "radiations",
        "surgery": "surgeries",
        "followups": "follow_ups",
    }
    for type in fields:
        if type in name_mappings:
            name = name_mappings[type]
        else:
            name = type
        ingest_str = f"/katsu/v2/ingest/{name}/"
        ingest_url = CANDIG_URL + ingest_str

        update_headers(headers)
        response = requests.post(
            ingest_url, headers=headers, data=json.dumps(fields[type])
        )

        if response.status_code == HTTPStatus.CREATED:
            result["results"].append(f"Of {len(fields[type])} {type}, {response.json()['result']} were created")
        elif response.status_code == HTTPStatus.NOT_FOUND:
            message = f"ERROR 404: {ingest_url} was not found! Please check the URL."
            result["errors"].append(response.text)
        else:
            message = f"\nREQUEST STATUS CODE: {response.status_code} \nRETURN MESSAGE: {response.text}\n"
            result["errors"].append(response.text)
    return result


def traverse_clinical_field(fields, field: dict, ctype, parents, types, ingested_ids):
    """
    Helper function for ingest_clinical_data. Parses and ingests clinical fields from a DonorWithClinicalData
    object.
    Args:
        field: The (sub)field of a DonorWithClinicalData object, potentially nested
        ctype: The type of the field being ingested (e.g. "donors")
        parents: A list of tuple mappings of the parents of this object, e.g.
            [
                ("donors", "DONOR_1"),
                ("primary_diagnoses", "PRIMARY_DIAGNOSIS_1")
            ]
        types: A list of possible field types
        id_names: A mapping of field names to what their ID key is (e.g. {"donors": "submitter_donor_id"})
        ingested_ids: A list of IDs that have already been ingested (some fields in DonorWithClinical are duplicates)
    """

    id_names = {
        "programs": "program_id",
        "donors": "submitter_donor_id",
        "primary_diagnoses": "submitter_primary_diagnosis_id",
        "sample_registrations": "submitter_sample_id",
        "treatments": "submitter_treatment_id",
        "specimens": "submitter_specimen_id",
        "followups": "submitter_follow_up_id",
    }

    data = {}
    if ctype in id_names:
        id_key = id_names[ctype]
    else:
        id_key = None
    if id_key:
        try:
            field_id = field.pop(id_key)
        except KeyError:
            raise ValueError(
                f"Missing required foreign key: {id_key} for {ctype} under {parents[-1][1]}"
            )
        if id_key not in ingested_ids:
            ingested_ids[id_key] = []
        if field_id in ingested_ids[id_key]:
            print(f"Skipping {field_id} in {id_key} (Already ingested).")
            return
        data[id_key] = field_id
        ingested_ids[id_key].append(field_id)

    attributes = list(field.keys())
    for attribute in attributes:
        if attribute not in types:
            data[attribute] = field.pop(attribute)

    if len(parents) >= 2:  # Program & donor have been added (must be the first 2 fields)
        foreign_keys = [parents[0], parents[1]]
        if len(parents) > 2:
            foreign_keys.append(parents[-1])
    else:
        foreign_keys = [parents[0]]  # Just program
    for parent in foreign_keys:
        parent_key = id_names[parent[0]]
        data[parent_key] = parent[1]

    fields[ctype].append(data)

    if id_key:
        parents.append((ctype, data[id_key]))
    subfields = field.keys()
    for subfield in subfields:
        if id_key:
            print(f"Loading {subfield} for {data[id_key]}...")
        else:
            print(f"Loading {subfield}...")
        if type(field[subfield]) == list:
            for elem in field[subfield]:
                traverse_clinical_field(
                    fields, elem, subfield, parents, types, ingested_ids
                )
        elif type(field[subfield]) == dict:
            traverse_clinical_field(
                fields, field[subfield], subfield, parents, types, ingested_ids
            )
    if id_key:
        parents.pop(-1)


def ingest_clinical_data(ingest_json, headers):
    """A single file ingest which validates and loads an MOH donor_with_clinical_data object from JSON.
    JSON format:
    [
        {
            "submitter_donor_id": ...,
            "program_id": ...,
            ...
            primary_site: {...},
            primary_diagnoses: {...},
            ...
        }
        ...
    ]
    (Fully outlined in MOH Schema)
    """
    schema = MoHSchema(ingest_json["openapi_url"])
    types = ["programs"]
    types.extend(schema.validation_schema.keys())

    # split ingest by program_id:
    donors_by_program = {}
    for donor in ingest_json["donors"]:
        if "program_id" not in donor:
            pass
        if donor["program_id"] not in donors_by_program:
            donors_by_program[donor["program_id"]] = {
                "donors": [],
                "errors": []
            }
        donors_by_program[donor["program_id"]]["donors"].append(donor)

    result = {}
    for program_id in donors_by_program.keys():
        errors = []
        print(f"Validating input for program {program_id}")
        schema.validate_ingest_map(donors_by_program[program_id])

        if len(schema.validation_warnings) > 0:
            print("Validation returned warnings:")
            print("\n".join(schema.validation_warnings))
        if len(schema.validation_errors) > 0:
            errors.append(
                "VALIDATION FAILED with the following issues",
                [str(line) for line in schema.validation_errors],
            )
            continue
        print("Validation success.")

        print("Beginning ingest")
        donors = donors_by_program[program_id].pop("donors")

        update_headers(headers)
        program_endpoint = "/katsu/v2/ingest/programs/"
        request = requests.Request(
            "POST",
            CANDIG_URL + program_endpoint,
            headers=headers,
            data=json.dumps(
                [{"program_id": program_id, "metadata": schema.statistics}]
            ),
        )
        if not auth.is_authed(request):
            return IngestPermissionsException(
                f"Not authorized to write to {program_id}"
            )
        response = requests.Session().send(request.prepare())
        if response.status_code != HTTPStatus.CREATED:
            if "unique" in response.text:
                errors.append(
                    f"Program {program_id} has already been ingested into Katsu. Please delete and try again."
                )
            else:
                errors.append(
                    [
                        f"\nREQUEST STATUS CODE: {response.status_code}"
                        f"\nRETURN MESSAGE: {response.text}\n"
                    ]
                )
        if len(errors) > 0:
            result[program_id] = {"errors": errors}
        else:
            result[program_id] = ingest_donors_for_program(donors, program_id, types, headers)
    return result


def ingest_donors_for_program(donors, program_id, types, headers):
    errors = []
    fields = {type: [] for type in types}
    for donor in donors:
        parents = [("programs", program_id)]
        print(f"Loading donor {donor['submitter_donor_id']}...")
        try:
            ingested_ids = {}
            traverse_clinical_field(fields, donor, "donors", parents, types, ingested_ids)
        except Exception as e:
            print(traceback.format_exc())
            errors.append(str(e))
    fields.pop("programs")
    # print(json.dumps(fields, indent=4))
    ingest_results = ingest_flattened(fields, headers)
    errors.extend(ingest_results["errors"])

    if len(errors) > 0:
        return {"errors": errors}
    else:
        return {"result": ingest_results["results"]}


def main():
    # check if os.environ.get("CANDIG_URL") is set
    if CANDIG_URL is None:
        print("ERROR: $CANDIG_URL is not set. Did you forget to run 'source env.sh'?")
        exit()
    headers = auth.get_auth_header()

    parser = argparse.ArgumentParser(description="A script that ingests clinical data into Katsu")
    parser.add_argument("--input", help="A file specifying the data to ingest")
    args = parser.parse_args()

    data_location = os.environ.get("CLINICAL_DATA_LOCATION")
    if not data_location:
        data_location = args.input
        if not data_location:
            print("ERROR: Could not find input data. Either --input is required or CLINICAL_DATA_LOCATION must be set.")
            exit()

    ingest_json = read_json(data_location)
    if "openapi_url" not in ingest_json:
        ingest_json["openapi_url"] = "https://raw.githubusercontent.com/CanDIG/katsu/develop/chord_metadata_service/mohpackets/docs/schema.yml"
    headers["Content-Type"] = "application/json"
    result = ingest_clinical_data(ingest_json, headers)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
