#!/usr/bin/python
#
# This is a free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This Ansible library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this library.  If not, see <http://www.gnu.org/licenses/>.

ANSIBLE_METADATA = {'metadata_version': '1.0',
                    'status': ['preview'],
                    'supported_by': 'community'}


DOCUMENTATION = '''
---
module: ec2_vpc_eigw
short_description: Manage an AWS VPC Egress Only Internet gateway
description:
    - Manage an AWS VPC Egress Only Internet gateway
version_added: "2.4"
author: Daniel Shepherd (@shepdelacreme)
options:
  vpc_id:
    description:
      - The VPC ID for the VPC that this Egress Only Internet Gateway should be attached.
    required: true
  state:
    description:
      - Create or delete the EIGW
    default: present
    choices: [ 'present', 'absent' ]
extends_documentation_fragment:
    - aws
    - ec2
'''

EXAMPLES = '''
# Note: These examples do not set authentication details, see the AWS Guide for details.

# Ensure that the VPC has an Internet Gateway.
# The Internet Gateway ID is can be accessed via {{eigw.gateway_id}} for use in setting up NATs etc.
ec2_vpc_eigw:
  vpc_id: vpc-abcdefgh
  state: present
register: eigw

'''

RETURN = '''
gateway_id:
    description: The ID of the Egress Only Internet Gateway
    returned: success
    type: string
    sample: eigw-0e00cf111ba5bc11e
'''


import traceback
from time import sleep
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.ec2 import (
    boto3_conn,
    ec2_argument_spec,
    get_aws_connection_info,
    AWSRetry,
    HAS_BOTO3,
    boto3_tag_list_to_ansible_dict,
    camel_dict_to_snake_dict,
    ansible_dict_to_boto3_filter_list
)
from botocore.exceptions import ClientError


@AWSRetry.backoff()
def delete_eigw(module, client, eigw_id):
    """
    Delete EIGW

    module     : AnsibleModule object
    client     : boto3 client connection object
    eigw_id    : ID of the EIGW to delete
    """

    try:
        response = client.delete_egress_only_internet_gateway(DryRun=module.check_mode, EgressOnlyInternetGatewayId=eigw_id)
    except ClientError as err:
        # When boto3 method is run with DryRun=True it returns an error on success
        # We need to catch the error and return something valid
        if err.response.get('Error').get('Code') == "DryRunOperation":
            return {'changed': True, 'gateway_id': None}
        else:
            module.fail_json(msg=err.message, exception=traceback.format_exc(), **camel_dict_to_snake_dict(err.response))

    return {'changed': response['ReturnCode']}


@AWSRetry.backoff()
def create_eigw(module, client, vpc_id):
    """
    Create EIGW

    module     : AnsibleModule object
    client     : boto3 client connection object
    vpc_id     : ID of the VPC we are operating on
    """

    try:
        response = client.create_egress_only_internet_gateway(DryRun=module.check_mode, VpcId=vpc_id)
        eigw = response['EgressOnlyInternetGateway']
    except ClientError as err:
        # When boto3 method is run with DryRun=True it returns an error on success
        # We need to catch the error and return something valid
        if err.response.get('Error').get('Code') == "DryRunOperation":
            return {'changed': True, 'gateway_id': None}
        else:
            module.fail_json(msg=err.message, exception=traceback.format_exc(), **camel_dict_to_snake_dict(err.response))

    # We loop through the Attachments list in boto3 response to make sure the EIGW is fully attached before returning
    state = eigw['Attachments'][0]['State']
    if state == 'attached':
        # EIGW is fully attached so we return immediately
        return {'changed': True, 'gateway_id': eigw['EgressOnlyInternetGatewayId']}
    elif state == 'attaching':
        # EIGW is still attaching so we check status
        retries = 5
        pause = 1
        while retries > 0:
            retries = retries - 1
            sleep(pause)
            try:
                check_resp = client.describe_egress_only_internet_gateways(EgressOnlyInternetGatewayIds=eigw['EgressOnlyInternetGatewayId'])
            except ClientError as err:
                module.fail_json(msg=err.message, exception=traceback.format_exc(), **camel_dict_to_snake_dict(err.response))

            if check_resp['EgressOnlyInternetGateways'][0]['Attachments'][0]['State'] == 'attached':
                    return {'changed': True, 'gateway_id': eigw['EgressOnlyInternetGatewayId']}
            pause = pause * 2
    else:
        # EIGW gave back a bad attachment state so we error out
        module.fail_json(msg='Unable to create and attach Egress Only Internet Gateway to VPCId: {0}. Bad Attachment State: {1}'.format(vpc_id, state))


@AWSRetry.backoff()
def describe_eigws(module, client, vpc_id):
    """
    Describe EIGWS

    module     : AnsibleModule object
    client     : boto3 client connection object
    vpc_id     : ID of the VPC we are operating on
    """

    try:
        response = client.describe_egress_only_internet_gateways()
    except ClientError as err:
        module.fail_json(msg=err.message, exception=traceback.format_exc(), **camel_dict_to_snake_dict(err.response))

    if len(response['EgressOnlyInternetGateways']) == 0:
        return None

    for eigw in response['EgressOnlyInternetGateways']:
        attached_vpc = eigw['Attachments'][0]['VpcId']
        state = eigw['Attachments'][0]['State']
        if attached_vpc == vpc_id and state in ('attached', 'attaching'):
            return eigw['EgressOnlyInternetGatewayId']
        else:
            return None


def main():
    argument_spec = ec2_argument_spec()
    argument_spec.update(dict(
        vpc_id=dict(required=True),
        state=dict(default='present', choices=['present', 'absent'])
    ))

    module = AnsibleModule(argument_spec=argument_spec, supports_check_mode=True)

    if not HAS_BOTO3:
        module.fail_json(msg='boto3 is required for this module')

    try:
        region, ec2_url, aws_connect_params = get_aws_connection_info(module, boto3=True)
        client = boto3_conn(module, conn_type='client', resource='ec2', **aws_connect_params)
    except ClientError as err:
        module.fail_json(msg=err.message, exception=traceback.format_exc(), **camel_dict_to_snake_dict(err.response))

    vpc_id = module.params.get('vpc_id')
    state = module.params.get('state')

    eigw_id = describe_eigws(module, client, vpc_id)

    result = dict(
        changed=False,
        gateway_id=eigw_id
    )

    if state == 'present' and not eigw_id:
        result = create_eigw(module, client, vpc_id)
    elif state == 'absent' and eigw_id:
        result = delete_eigw(module, client, eigw_id)

    module.exit_json(**result)


if __name__ == '__main__':
    main()
