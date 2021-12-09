[![Tests](https://github.com/papermerge/papermerge-core/actions/workflows/pep8.yml/badge.svg)](https://github.com/papermerge/papermerge-core/actions/workflows/pep8.yml)

# Papermerge REST API Backend Server


This python package is the heart of Papermerge project. It consists of a set
of reusable Django apps which are consumed across different bundles of
Papermerge Document Management System (DMS).

Technically speaking, it contains two django apps:

* ``papermerge.core`` - the epicenter of papermerge project
* ``papermerge.notifications`` - Django Channels app for sending notifications via websockets

This package is intended to be part of Django project [like this one](https://github.com/ciur/papermerge/) for example.

## What is Papermerge?

Papermerge is an open source document management system (DMS) primarily
designed for archiving and retrieving your digital documents. Instead of
having piles of paper documents all over your desk, office or drawers - you
can quickly scan them and configure your scanner to directly upload to
Papermerge DMS. Papermerge DMS on its turn will extract text data from the
scanned documents using Optical Character Recognition (OCR) technology the
index it and make it searchable. You will be able to quickly find any
(scanned!) document using full text search capabilities.

Papermerge is perfect tool to manage PDF, JPEG, TIFF and PNG formats.

## Features Highlights

* REST API
* Documents of pdf, jpg, png, tiff formats are supported
* Per page OCR (Optical Character Recognition) of the documents
* Full Text Search of the scanned documents
* Document Versions
* User defined metadata per folder/document/page
* Tags - assign colored tags to documents or folders
* Documents and Folders - users can organize documents in folders
* Multi-User (Groups, Roles)
* User permissions management
* Document permissions management
* Page Management - delete, reorder, cut & paste pages
* Automation

## How to Run Tests

Use [poetry](https://python-poetry.org/) to switch into python virtual environment:

    $ poetry shell

Then install all dependencies in current python virtual environment:

    $ poetry install

And finally run tests:

    $ PYTHONPATH=. pytest

Another way to invoke [pytest](https://docs.pytest.org/en/latest/contents.html), which automatically adds current working directory to PYTHONPATH:

    $ python -m pytest

Disable warning during test runs:

    $ python -m pytest --disable-warnings

One handy shortcut to invoke pytests in python virtual environment:

    $ poetry run python -m pytest --disable-warnings
