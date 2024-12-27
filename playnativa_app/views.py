from django.shortcuts import render
from django.http import HttpResponse

def autopoiesis(request):
    return render(request, 'autopoiesis.html')


def root_view(request):
    return HttpResponse("OK", status=200)
