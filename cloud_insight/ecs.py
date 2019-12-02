#!/usr/bin/python

import cloud_insight.aws as aws
import cloud_insight.dynamodb as dynamodb
import cloud_insight.elb as elb
import datetime


# DESCRIBE SERVICES
def describe_service(client, service_names, cluster_name):
    service_description = client.describe_services(
        cluster=cluster_name,
        services=[service_names]
    )
    return service_description


# DESCRIBE TASK
def describe_task(client, cluster_name, task_id):
    task_description = client.describe_tasks(
        cluster=cluster_name,
        tasks=[task_id]
    )
    return task_description


# DESCRIBE TASK DEFINITION
def describe_task_definition(client, task_definition_name):
    task_definition_description = client.describe_task_definition(
        taskDefinition=task_definition_name
    )
    return task_definition_description


# GET CONTAINER UPTIME
def get_uptime(app, client, cluster_name, service_name):

    uptime_list = []

    for task in list_tasks(client, cluster_name, service_name):

        try:

            start_time = describe_task(client, cluster_name, task)['tasks'][0]['startedAt']

            current_time = datetime.datetime.now(start_time.tzinfo)

            uptime = current_time.replace(microsecond=0) - start_time.replace(microsecond=0)

            uptime_list.append(uptime)

        except Exception:

            app.log.error('Something went wrong trying to get uptime')

    return uptime_list


# LIST CLUSTERS
def list_clusters(client):
    cluster_names = client.list_clusters()
    return cluster_names


# LIST ALL SERVICES IN A CLUSTER
def list_services(app, client, cluster_name):
    paginator = client.get_paginator('list_services')
    page_iterator = paginator.paginate(
        cluster=cluster_name
    )
    service_names = []
    for page in page_iterator:
        service_names.extend(page['serviceArns'])
    app.log.info('Found {0} Services'.format(len(service_names)))
    return service_names


# LIST ALL TASKS IN A CLUSTER
def list_tasks(client, cluster_name, service_name):
    paginator = client.get_paginator('list_tasks')
    page_iterator = paginator.paginate(
        cluster=cluster_name,
        serviceName=service_name
    )
    tasks = []
    for page in page_iterator:
        tasks.extend(page['taskArns'])
    return tasks


def service_dictionary(app, aws_region, aws_session, namespace):

    ecs_client = aws.session_client(app, aws_region, 'ecs', aws_session)
    ecs_services = []

    # ITERATE THROUGH ECS CLUSTERS
    for ecs_cluster in list_clusters(ecs_client)['clusterArns']:

        # PRINT CLUSTERS
        app.log.info('AWS: Found cluster {0}'.format(
            aws.parse_arn(ecs_cluster)['resource'])
        )

        # ITERATE THROUGH SERVICES IN EACH CLUSTER
        for ecs_service in list_services(app, ecs_client, aws.parse_arn(ecs_cluster)['resource']):

            # CREATE SERVICE DICTIONARY
            service = dict()
            ecs_service_name = aws.parse_arn(ecs_service)['resource']

            # ADD SERVICE ITEM TO DICTIONARY
            service['service'] = ecs_service_name

            # ADD CLUSTER ITEM TO DICTIONARY
            service['cluster'] = aws.parse_arn(ecs_cluster)['resource']

            service['region'] = ecs_client.meta.region_name

            # PRINT SERVICES
            app.log.info('AWS: Found service {0}'.format(
                ecs_service_name)
            )

            # DESCRIBE SERVICES
            ecs_service_description = describe_service(
                ecs_client,
                ecs_service_name,
                aws.parse_arn(ecs_cluster)['resource']
            )

            # DESCRIBE TASK DEFINITIONS
            ecs_task_description = describe_task_definition(
                ecs_client,
                ecs_service_description['services'][0]['taskDefinition']
            )

            # ADD VERSION ITEM TO DICTIONARY
            service['version'] = \
                ecs_task_description['taskDefinition']['containerDefinitions'][0]['image'].split(':', 1)[-1]

            try:
                service['created_at'] = ecs_service_description['services'][0]['deployments'][0]['createdAt']
                service['updated_at'] = ecs_service_description['services'][0]['deployments'][0]['updatedAt']
            except Exception:
                service['created_at'] = "unknown"
                service['updated_at'] = "unknown"

            # # PRINT SERVICE DESCRIPTION
            # app.log.info('AWS: Service {0}, Count {1}, Active Task Definition {2}'.format(
            #     aws.parse_arn(ecs_service)['resource'],
            #     ecs_service_description['services'][0]['desiredCount'],
            #     ecs_service_description['services'][0]['taskDefinition'])
            # )

            if namespace == 'connectivity':

                if ecs_service_description['services'][0]['loadBalancers']:

                    elb_client = aws.session_client(
                        app,
                        aws_region,
                        'elbv2',
                        aws_session
                    )

                    target_group_arns = ecs_service_description['services'][0]['loadBalancers'][0]['targetGroupArn']

                    load_balancer_arns = elb.describe_target_groups(
                        elb_client,
                        target_group_arns=[target_group_arns]
                    )[0]['LoadBalancerArns']

                    load_balancer_description = elb.describe_load_balancers(
                        elb_client,
                        load_balancer_arns=load_balancer_arns
                    )[0]

                    service['alb_name'] = load_balancer_description['LoadBalancerName']
                    service['alb_scheme'] = load_balancer_description['Scheme']

                    listener_arn = elb.describe_listeners(
                        elb_client,
                        load_balancer_arn=load_balancer_arns[0]
                    )[0]['ListenerArn']

                    rules_description = elb.describe_rules(
                        elb_client,
                        listener_arn
                    )

                    routing_paths = []

                    if load_balancer_description['Type'] == 'application':

                        for rule in rules_description['Rules']:
                            if rule['Actions'][0]['TargetGroupArn'] == target_group_arns:
                                # print(rule['Conditions'][0]['Values'])
                                routing_paths.extend(
                                    rule['Conditions'][0]['Values']
                                )

                    service['paths'] = routing_paths

                else:

                    service['alb_name'] = ''
                    service['alb_scheme'] = ''
                    service['paths'] = ''

            if namespace == 'health':
                ecs_uptime_list = get_uptime(
                    app,
                    ecs_client,
                    aws.parse_arn(ecs_cluster)['resource'],
                    ecs_service_name
                )
                min_max_avg_list = []
                if ecs_uptime_list:
                    min_max_avg_list.append(min(ecs_uptime_list))
                    min_max_avg_list.append(max(ecs_uptime_list))
                    min_max_avg_list.append(reduce(lambda x, y: (x + y) / 2, ecs_uptime_list))
                    service['min_uptime'] = '{}'.format(min_max_avg_list[0])
                    service['max_uptime'] = '{}'.format(min_max_avg_list[1])
                    service['avg_uptime'] = '{}'.format(min_max_avg_list[2])
                elif not ecs_uptime_list:
                    service['min_uptime'] = 'N/A'
                    service['max_uptime'] = 'N/A'
                    service['avg_uptime'] = 'N/A'
                else:
                    app.log.error('An error occurred trying to parse the uptime list')

                # ADD COUNT INFORMATION TO DICTIONARY
                service['desired_count'] = ecs_service_description['services'][0]['desiredCount']
                service['running_count'] = ecs_service_description['services'][0]['runningCount']

            if namespace == 'history':

                dynamodb_resource = aws.session_resource(
                    app,
                    aws_region,
                    'dynamodb',
                    aws_session
                )

                task_definition_history = dynamodb.get_task_definitions(
                    app,
                    dynamodb_resource,
                    ecs_service_name,
                    app.config.get_section_dict('aws')['ecs-events']['table']
                )

                service['history'] = []

                for task_definition in task_definition_history:

                    version = describe_task_definition(
                        ecs_client,
                        task_definition
                    )['taskDefinition']['containerDefinitions'][0]['image'].split(':', 1)[-1]

                    if version not in service['history']:
                        service['history'].append(version)

            if namespace == 'list':

                try:
                    service['soft_memory'] = ecs_task_description['taskDefinition']['containerDefinitions'][0]['memoryReservation']
                    service['hard_memory'] = ecs_task_description['taskDefinition']['containerDefinitions'][0]['memory']
                except Exception:
                    service['soft_memory'] = "unknown"
                    service['hard_memory'] = "unknown"

                # GET ALL TASKS UPTIME
                ecs_uptime_list = get_uptime(
                    app,
                    ecs_client,
                    aws.parse_arn(ecs_cluster)['resource'],
                    aws.parse_arn(ecs_service)['resource']
                )

                min_max_avg_list = []

                if ecs_uptime_list:

                    min_max_avg_list.append(min(ecs_uptime_list))

                    min_max_avg_list.append(max(ecs_uptime_list))

                    min_max_avg_list.append(reduce(lambda x, y: (x + y) / 2, ecs_uptime_list))

                    service['min_uptime'] = '{}'.format(min_max_avg_list[0])
                    service['max_uptime'] = '{}'.format(min_max_avg_list[1])
                    service['avg_uptime'] = '{}'.format(min_max_avg_list[2])

                elif not ecs_uptime_list:

                    service['min_uptime'] = 'N/A'
                    service['max_uptime'] = 'N/A'
                    service['avg_uptime'] = 'N/A'

                else:

                    app.log.error('An error occurred trying to parse the uptime list')

                # ADD COUNT INFORMATION TO DICTIONARY
                service['desired_count'] = ecs_service_description['services'][0]['desiredCount']
                service['running_count'] = ecs_service_description['services'][0]['runningCount']

                # ADD LAUNCH TYPE INFORMATION TO DICTIONARY
                service['launch_type'] = ecs_service_description['services'][0]['launchType']

            # APPEND DICTIONARY ITEMS TO ARRAY
            ecs_services.append(service)

    sorted_ecs_services = sorted(ecs_services, key=lambda k: k['service'])

    return sorted_ecs_services
