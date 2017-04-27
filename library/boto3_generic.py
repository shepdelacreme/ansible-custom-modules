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
module: boto3_generic
short_description: Generic interface to boto3
description:
    - Manage an AWS VPC Egress Only Internet gateway
version_added: "2.4"
author:
    - "Daniel Shepherd (@shepdelacreme)"
requirements:
  - boto3
  - botocore
options:
  service:
    description:
      - The AWS service you want to access with boto3. The service name must match the definitions in the boto3 docs.
      - Example ec2, s3, cloudtrail, etc
      - See U(http://boto3.readthedocs.io/en/latest/reference/services/index.html)
    required: true
  conn_type:
    description:
      - The type of boto3 connection to create.
      - boto3 provides a low-level 'client' interface as well as a higher level 'resource' interface that abstracts some functionality.
    default: client
    choices: [ 'client', 'resource' ]
  operation_name:
    description:
      - The method or name of the operation of the boto3 client/resource to use. This needs to be specified as documented in the boto3 docs or the AWS CLI/API
      - Both the Camel Case and Snake Case version of the name is accepted here.
      - See U(http://boto3.readthedocs.io/en/latest/reference/services/index.html)
    required: true
  arguments:
    description:
      - Complex arguments to pass to the boto3 service method specified above.
    aliases: args
extends_documentation_fragment:
    - aws
'''

EXAMPLES = '''
# Note: These examples do not set authentication details, see the AWS Guide for details.

# Ensure that the VPC has an Internet Gateway.
# The Internet Gateway ID is can be accessed via {{eigw.gateway_id}} for use in setting up NATs etc.
boto3_generic:
  service: ec2
  conn_type: client
  operation_name: describe_vpcs
  args:
      VpcIds: vpc-83cce1e6
register: vpcs

'''

RETURN = '''
response:
    description: The response from boto3 converted to snake dict.
    returned: always
    type: complex
    sample: hash/dictionary returned by boto3 low-level client
    contains:
      response_metadata:
        description: hash/dictionary with the parsed HTTP response
        returned: always
        type: dict
        contains:
          http_headers:
            description: HTTP headers from the response parsed out
            type: dict
            contains:
              content_type:
                description: HTTP content-type
                returned: always
                sample: "text/html;charset=UTF-8"
              date:
                description: Date of request
                returned: always
                sample: "Tue, 25 Apr 2017 17:40:43 GMT"
              server:
                description: HTTP server type
                returned: always
                sample: "AmazonEC2"
              transer-encoding:
                description: Transer encoding header
                returned: always
                sample: "chunked"
              vary:
                description: don't know
                returned: always
                sample: "Accept-Encoding"
          http_status_code:
            description: The HTTP status code of the response
            returned: always
            sample: 200
          request_id:
            description: The unique request id assigned to the request
            returned: always
            sample: c646d2d2-29e0-11e7-93ae-92361f002671
          retry_attemps:
            description: The number of times the request was retried
            returned: always
            sample: 0
      other:
        description: This is a boto3 operation specific object that is returned. Consult the boto3 documentation for the operation being invoked.
          - The name will not be "other" and is specific to the operation.
          - This is typically a list of dicts or a single dict.
        returned: sometimes
        sample: This is a complex type, usually a list of dicts or a dict.
'''


import traceback
import re
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
from botocore.session import Session
from botocore.exceptions import ClientError


def camel_to_snake(name):

    first_cap_re = re.compile('(.)([A-Z][a-z]+)')
    all_cap_re = re.compile('([a-z0-9])([A-Z])')
    s1 = first_cap_re.sub(r'\1_\2', name)

    return all_cap_re.sub(r'\1_\2', s1).lower()


def call_boto3_operation(module, conn, operation_name, args):

    try:
        boto3_call = getattr(conn, operation_name)
        response = camel_dict_to_snake_dict(boto3_call(**args))
    except ClientError as err:
        # When boto3 method is run with DryRun=True it returns an error on success
        # We need to catch the error and return something valid
        if err.response.get('Error').get('Code') == "DryRunOperation":
            return {'changed': True}
        else:
            return camel_dict_to_snake_dict(err.response)

    return response


def validate_params(module, service, operation_name, args, **awsparams):
    profile = awsparams.get('profile_name')

    session = Session(profile=profile)

    if service not in session.get_available_services():
        module.fail_json(msg='Invalid Service Name: {0}'.format(service))

    service_model = session.get_service_model(service)
    op_names = list(service_model.operation_names)
    if operation_name not in op_names:
        module.fail_json(msg='Invalid Operation Name: {0} for Service: {1}'.format(operation_name, service))

    op_model = service_model.operation_model(operation_name)
    shape_members = dict(op_model.input_shape.members)
    required_members = list(op_model.input_shape.required_members)

    if 'DryRun' not in shape_members:
        args.pop('DryRun')

    bad_params = set(args.keys()) - set(shape_members.keys())
    if bad_params:
        module.fail_json(msg='Invalid Argument(s): {0} for Service Operation: {1}'.format(", ".join(bad_params), operation_name))

    missing_params = set(required_members) - set(args.keys())
    if missing_params:
        module.fail_json(msg='Missing Required Argument(s): {0} for Service Operation: {1}'.format(", ".join(missing_params), operation_name))

    return args


def main():
    argument_spec = ec2_argument_spec()
    argument_spec.update(dict(
        arguments=dict(aliases=['args'], default=dict(), type='dict'),
        service=dict(required=True),
        conn_type=dict(default='client', choices=['client', 'resource']),
        operation_name=dict(required=True)
    ))

    module = AnsibleModule(argument_spec=argument_spec, supports_check_mode=True)

    if not HAS_BOTO3:
        module.fail_json(msg='boto3 is required for this module')

    if isinstance(module.params['arguments'], dict):
        args = module.params['arguments']
    else:
        args = dict()

    service = module.params['service']
    conn_type = module.params['conn_type']
    operation_name = module.params['operation_name']

    args['DryRun'] = module.check_mode

    if args.get('Filters'):
        args['Filters'] = ansible_dict_to_boto3_filter_list(args['Filters'])

    result = dict(
        changed=False
    )

    try:
        region, ec2_url, aws_connect_params = get_aws_connection_info(module, boto3=True)
        args = validate_params(module, service, operation_name, args, **aws_connect_params)
        conn = boto3_conn(module, conn_type=conn_type, resource=service, **aws_connect_params)
    except ClientError as err:
        module.fail_json(msg=err.message, exception=traceback.format_exc(), **camel_dict_to_snake_dict(err.response))

    result['response'] = call_boto3_operation(module, conn, camel_to_snake(operation_name), args)

    module.exit_json(**result)


if __name__ == '__main__':
    main()
