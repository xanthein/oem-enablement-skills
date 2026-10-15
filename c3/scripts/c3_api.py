#!/bin/env python3

import base64
import configparser
import logging
import requests


class c3api:
    def __init__(self, c3_secrets_file):
        self.c3_url = f'https://certification.canonical.com'
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(message)s')

        c3_client_id, c3_client_secret = self.get_c3_secrets(c3_secrets_file)
        try:
            self.access_token = self.get_access_token(c3_client_id, c3_client_secret)
        except Exception as e:
            logging.warning(f"An unexpected error occurred: {e}")
            raise

    def get_access_token(self, client_id, secret):
        credential = base64.b64encode("{0}:{1}".format(client_id, secret).encode("utf-8")).decode("utf-8")
        headers = {
            "Authorization": "Basic {0}".format(credential),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        params = {
            "grant_type": "client_credentials",
            "scope": "read write"
        }

        response = requests.post(
            f"{self.c3_url}/oauth2/token/", headers=headers, data=params)
        response.raise_for_status()
        return response.json()["access_token"]

    def c3_get(self, endpoint, **params):
        headers = {
        "Authorization": "Bearer {0}".format(self.access_token)
        }
        params.update({"pagination": "limitoffset", "limit": 0})
        try:
            response = requests.get(f'{self.c3_url}/api/v2/{endpoint}', headers=headers, params=params)
            response.raise_for_status()
        except Exception as e:
            logging.error(e)
            logging.warning(f"{endpoint} query fail!")
            return None

        if 'results' in response.json().keys():
            results = response.json()['results']
        else:
            results = response.json()
        return results

    def c3_patch(self, endpoint, data):
        headers = {
            "Authorization": "Bearer {0}".format(self.access_token)
        }
        #params.update({"pagination": "limitoffset", "limit": 0})
        try:
            response = requests.patch(f'{self.c3_url}/api/v2/{endpoint}', headers=headers, json=data)
            response.raise_for_status()
        except Exception as e:
            logging.debug(e)
            logging.warning(f"{endpoint} patch fail!")
            return None

        results = response.json()
        logging.debug(results)
        return results

    def c3_post(self, endpoint, data):
        headers = {
            "Authorization": "Bearer {0}".format(self.access_token),
        }
        #params.update({"pagination": "limitoffset", "limit": 0})
        response = requests.post(f'{self.c3_url}/api/v2/{endpoint}/', headers=headers, json=data)

        if response.status_code != 201:
            logging.warning(f"{endpoint} post fail!")
            return None

        results = response.json()
        logging.debug(results)
        return results

    def get_linked_labresource(self, cid):
        linked_labresources = self.c3_get('linked-labresources/',
                                           canonical_ids=f'{cid}')
        if linked_labresources:
            return linked_labresources[f'{cid}']
        return None

    def get_ip_address(self, cid):
        labresource = self.get_linked_labresource(cid)
        if labresource:
            return labresource.get('ip_address') or None
        return None

    def find_machines_by_label(self, canonical_label, holder=None):
        params = {'canonical_label': canonical_label}
        if holder:
            params['holder__name'] = holder
        return self.c3_get('machines/', **params) or []

    def get_ip_by_canonical_label(self, canonical_label, holder=None):
        machines = self.find_machines_by_label(canonical_label, holder)
        if not machines:
            logging.error(f'No machine found for canonical_label {canonical_label}')
            return None

        if len(machines) > 1:
            logging.error(
                f'Multiple machines found for canonical_label {canonical_label}, '
                f'please disambiguate with --holder:')
            for machine in machines:
                holder_name = (machine.get('holder') or {}).get('name')
                logging.error(f'  canonical_id={machine["canonical_id"]} holder={holder_name}')
            return None

        return self.get_ip_address(machines[0]['canonical_id'])

    def detach_labresource(self, labresource_id):
        logging.info(f'Detach labresource {labresource_id}')
        null_cid = {'canonical_id': None}
        self.c3_patch(f'labresources/{labresource_id}/', data=null_cid)

    def find_labresource(self, lab_room, frame, shelf, partition):
        labresources = self.c3_get('labresources/',
                                    datacentre__name=f'TEL-L{lab_room}',
                                    locations__frame__identifier=f'F{frame}',
                                    role='DUT')
        for item in labresources:
            if int(shelf) == item["locations"][0]["shelf"] and int(partition) == item["locations"][0]["partition"]:
                return item
        return None

    def link_labresource(self, labresource_id, cid):
        logging.info(f'Update labresources {labresource_id}')
        update_cid = {'canonical_id': f'{cid}'}
        self.c3_patch(f'labresources/{labresource_id}/', data=update_cid)

    def update_machine_location(self, cid, lab_room):
        logging.info(f'Update machine {cid}')
        update_location = {'holder': 'kevinyeh', 'location': f'Taipei Eongher - Lab-{lab_room}'}
        self.c3_patch(f'machines/{cid}/', data=update_location)

    def update_c3_info(self, cid, data):
        # detach old labresource
        linked_labresource = self.get_linked_labresource(data.cid)
        if linked_labresource:
            self.detach_labresource(linked_labresource['id'])

        item = self.find_labresource(data.lab_room, data.frame, data.shelf, data.partition)
        if item is None:
            return None

        if item["canonical_id"] is None:
            # link labresource
            self.link_labresource(item["id"], cid)
            # update holder and location
            self.update_machine_location(cid, data.lab_room)
            return True
        else:
            logging.error(f'{item["canonical_id"]} already register in {item["id"]}')
            return False

    def get_c3_secrets(self, c3_secrets_file):
        config = configparser.ConfigParser()
        logging.debug(c3_secrets_file)
        config.read(c3_secrets_file)
        client_id = config['c3 secret']['client_id']
        client_secret = config['c3 secret']['client_secret']
        return client_id, client_secret


if __name__ == '__main__':
    import argparse
    import sys

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(message)s')
    parser = argparse.ArgumentParser(
        description='CLI for interacting with the C3 API',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--secrets', dest='secrets', required=True,
                         help='file storing c3api secrets')

    subparsers = parser.add_subparsers(dest='command', required=True)

    get_ip_parser = subparsers.add_parser('get-ip', help='Get the IP address for a CID')
    get_ip_parser.add_argument('cid', help='Canonical ID of the machine')

    get_ip_by_label_parser = subparsers.add_parser(
        'get-ip-by-label', help='Get the IP address for a canonical_label')
    get_ip_by_label_parser.add_argument('canonical_label', help='Canonical label of the machine')
    get_ip_by_label_parser.add_argument(
        '--holder', help='Holder name, used to disambiguate machines sharing the same canonical_label')

    args = parser.parse_args()
    api = c3api(args.secrets)

    if args.command == 'get-ip':
        ip_address = api.get_ip_address(args.cid)
        if ip_address:
            print(ip_address)
        else:
            logging.error(f'No IP address found for {args.cid}')
            sys.exit(1)
    elif args.command == 'get-ip-by-label':
        ip_address = api.get_ip_by_canonical_label(args.canonical_label, args.holder)
        if ip_address:
            print(ip_address)
        else:
            sys.exit(1)
