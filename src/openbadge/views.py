from functools import wraps
import datetime
import analysis
import simplejson

from dateutil.parser import parse as parse_date
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound, JsonResponse
from django.shortcuts import render

from rest_framework.decorators import api_view
from rest_framework import viewsets
from rest_framework.response import Response

from .decorators import app_view, is_god, is_own_project
from .models import Meeting, Project, Hub  # Chunk  # ActionDataChunk, SamplesDataChunk

from .models import Member
from .serializers import MemberSerializer, HubSerializer


TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class HttpResponseUnauthorized(HttpResponse):
    status_code = 401


def json_response(**kwargs):
    return HttpResponse(simplejson.dumps(kwargs))


def context(**extra):
    return dict(**extra)


class MemberViewSet(viewsets.ModelViewSet):
    queryset = Member.objects.all()
    serializer_class = MemberSerializer
    lookup_field = 'key'

class HubViewSet(viewsets.ModelViewSet):
    queryset = Hub.objects.all()
    serializer_class = HubSerializer
    lookup_field = 'name'




@app_view
def test(request):
    return json_response(success=True)


def test_error(request):
    raise simplejson.JSONDecodeError
    return HttpResponse()


def render_to(template):
    def decorator(func):
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            out = func(request, *args, **kwargs)
            if isinstance(out, dict):
                out = render(request, template, out)
            return out

        return wrapper

    return decorator


## No-Group views ######################################################################################################

###########################
# Project Level Endpoints #
###########################

@app_view
@api_view(['PUT', 'GET'])
def projects(request):
    if (request.method == 'PUT'):
        return put_project(request)
    elif (request.method == 'GET'):
        return get_project(request)
    return HttpResponseNotFound()


@is_god
@api_view(['PUT'])
def put_project(request):
    return json_response(status="Not Implemented")


@api_view(['GET'])
def get_project(request):
    hub_uuid = request.META.get("HTTP_X_HUB_UUID")
    """ng-device's id for the hub submitting this request"""

    if not hub_uuid:
        return HttpResponseBadRequest()

    try:
        hub = Hub.objects.prefetch_related("project").get(uuid=hub_uuid)
    except Hub.DoesNotExist:
        return HttpResponseNotFound()

    project = hub.project  # type: Project

    return JsonResponse(project.to_object())


###########################
# Meeting Level Endpoints #
###########################

@is_own_project
@app_view
@api_view(['PUT', 'GET', 'POST'])
def meetings(request, project_key):
    if request.method == 'PUT':
        return put_meeting(request, project_key)
    elif request.method == 'GET':
        return get_meeting(request, project_key)
    elif request.method == 'POST':
        return post_meeting(request, project_key)
    return HttpResponseNotFound()


@api_view(['PUT'])
def put_meeting(request, project_key):
    log_file = request.FILES.get("file")

    hub_uuid = request.META.get("HTTP_X_HUB_UUID")

    meeting_meta = simplejson.loads(log_file.readline())
    log_file.seek(0)

    meeting_data = meeting_meta['data']
    meeting_uuid = meeting_data['uuid']

    try:
        meeting = Meeting.objects.get(uuid=meeting_uuid)
        if meeting.hub.uuid != hub_uuid:
            return HttpResponseUnauthorized()

    except Meeting.DoesNotExist:
        meeting = Meeting()
        meeting.uuid = meeting_uuid
        meeting.version = meeting_data['log_version']
        meeting.project = Project.objects.get(key=project_key)

    try:
        log_file.seek(-2, 2)  # Jump to the second last byte.
        while log_file.read(1) != b"\n":  # Until EOL is found...
            log_file.seek(-2, 1)  # ...jump back the read byte plus one more.

        last = log_file.readline()  # Read last line.

        last_log = simplejson.loads(last)
        meeting.last_update_index = last_log['log_index']
        meeting.last_update_timestamp = last_log['log_timestamp']
    except IOError:
        pass

    log_file.seek(0)

    meeting.log_file = log_file

    meeting.hub = Hub.objects.get(uuid=hub_uuid)

    meeting.start_time = meeting_data["start_time"]

    meeting.is_complete = request.data["is_complete"] == 'true' if 'is_complete' in request.data else False

    if meeting.is_complete:
        meeting.ending_method = request.data['ending_method']
        meeting.end_time = meeting.last_update_timestamp

    meeting.save()

    if meeting.is_complete and settings.SEND_POST_MEETING_SURVEY:
        analysis.post_meeting_analysis(meeting)

    return JsonResponse({'detail': 'meeting created'})


@api_view(['GET'])
def get_meeting(request, project_key):
    try:
        project = Project.objects.prefetch_related("meetings").get(key=project_key)
        get_file = str(request.META.get("HTTP_X_GET_FILE")).lower() == "true"

        return JsonResponse(project.get_meetings(get_file))

    except Project.DoesNotExist:
        return HttpResponseNotFound()


@api_view(['POST'])
def post_meeting(request, project_key):

    meeting = Meeting.objects.get(uuid=request.data.get('uuid'))
    chunks = request.data.get('chunks')
    meeting.is_complete = False  # Make sure we always close a meeting with a PUT.
    update_index = None
    update_time = None

    print meeting.hub.name + " appending",
    chunks = simplejson.loads(chunks)
    if len(chunks) == 0:
        print " NO CHUNKS",
    else:
        post_start_serial = simplejson.loads(chunks[0])['log_index']
        if post_start_serial != meeting.last_update_index + 1:
            #meeting.last_update_serial = -1
            meeting.save()
            return JsonResponse({"status": "log mismatch"})
        print "chunks",

    log = meeting.log_file.file.name
    with open(log, 'a') as f:
        for chunk in chunks:
            chunk_obj = simplejson.loads(chunk)
            update_time = chunk_obj['log_timestamp']
            update_index = chunk_obj['log_index']
            print update_index,
            f.write(chunk)

    print "to", meeting


    if update_time and update_index:
        meeting.last_update_timestamp = update_time      # simplejson.loads(chunks[-1])['last_log_time']
        meeting.last_update_index = update_index   # simplejson.loads(chunks[-1])['last_log_serial']

    meeting.save()

    return JsonResponse({"status": "success"})


#######################
# Hub Level Endpoints #
#######################

@app_view
@api_view(['PUT', 'GET', 'POST'])
def hubs(request, project_key):
    if request.method == 'PUT':
        return put_hubs(request, project_key)
    elif request.method == 'GET':
        return get_hubs(request, project_key)
    elif request.method == 'POST':
        return post_hubs(request, project_key)
    return HttpResponseNotFound()


@api_view(['PUT'])
def put_hubs(request, project_key):
    hub_uuid = request.META.get("HTTP_X_HUB_UUID")
    if hub_uuid:
        hub = Hub()
        hub.uuid = hub_uuid
        hub.project = Project.objects.get(name="OB-DEFAULT")
        hub.name = "New Hub"
        hub.save()
        return HttpResponse()

    return HttpResponseBadRequest()


@is_own_project
@api_view(['GET'])
def get_hubs(request, project_key):
    hub_uuid = request.META.get("HTTP_X_HUB_UUID")
    last_update = request.META.get("HTTP_X_LAST_MEMBER_UPDATE")
    if not hub_uuid:
        return HttpResponseBadRequest()
    try:
        hub = Hub.objects.get(uuid=hub_uuid)
    except Hub.DoesNotExist:
        return HttpResponseNotFound()
    if not last_update:
        return JsonResponse(hub.get_object(0))
    return JsonResponse(hub.get_object(float(last_update)-10)) # account for some amount of async behaviour


@is_own_project
@is_god
@api_view(['POST'])
def post_hubs(request, project_key):
    return JsonResponse({"status": "Not Implemented"})


#########################
# Badge Level Endpoints #
#########################

@is_own_project
@app_view
@api_view(['PUT', 'GET', 'POST'])
def members(request, project_key):
    if request.method == 'PUT':
        return put_members(request, project_key)
    elif request.method == 'GET':
        return get_members(request, project_key)
    elif request.method == 'POST':
        return post_members(request, project_key)
    return HttpResponseNotFound()


@is_god
@api_view(['PUT'])
def put_members(request, project_key):
    return JsonResponse({"status": "Not Implemented"})


@is_god
@api_view(['GET'])
def get_members(request, project_key):
    return JsonResponse({"status": "Not Implemented"})


@api_view(['POST'])
def post_members(request, project_key):

    return JsonResponse({"status": "Not Implemented"})
