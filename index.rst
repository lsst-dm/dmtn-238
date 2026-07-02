############################################
RSP DataLink service implementation strategy
############################################

.. abstract::

   The Rubin Science Platform provides an `IVOA TAP`_ service that returns structured information about Rubin Observatory data products.
   To allow easy user exploration of that data, particularly via the Portal Aspect, we've identified at least three types of information that we may want to associate with the returned records: how to download the image associated with an observation, how to retrieve a cutout of that image, and additional related TAP queries that the user may wish to explore.
   We expect to have additional similar use cases in the future.
   This tech note describes how we meet those requirements using the `IVOA DataLink`_ protocol and the datalinker_ web service.

.. _IVOA TAP: https://www.ivoa.net/documents/TAP/
.. _IVOA DataLink: https://www.ivoa.net/documents/DataLink/
.. _datalinker: https://github.com/lsst-sqre/datalinker

DataLink introduction
=====================

The `IVOA DataLink`_ standard provides a mechanism to link from metadata about datasets to the data itself, related data products, and web services that can operate on the data.
Most directly relevant for the purposes of this tech note, it defines two standards: a web service that provides links for a given identifier, and a general way of extending a VOTable_ with links to associated services.
We use the first standard to provide access to the image associated with a record and a pointer to the image cutout service, and the second to provide pointers to related TAP queries.

.. _VOTable: https://www.ivoa.net/documents/VOTable/

The links service is a DALI_ web service that takes as input one or more identifiers and returns a VOTable of links and services associated with that identifier.
Here is an example from the Science Platform links service:

.. literalinclude:: examples/links.xml
   :language: xml

The full URL to the image (a signed GCS or S3 URL, see :ref:`links-service`) has been replaced for ``URL`` for brevity.

.. _DALI: https://www.ivoa.net/documents/DALI/

The ``cutout-sync`` resource above is an example of adding service descriptors to a VOTable.
This can be done to any VOTable, not just the links table returned by the links service.
For example, here is a service descriptor that we attach to a TAP result on the ``Object`` table that allows the user to retrieve a light curve for the object.

.. literalinclude:: examples/descriptor.xml
   :language: xml

A client aware of the relevant IVOA protocols, such as the Portal Aspect, can use these service descriptors to prompt the user for additional required parameters for the linked services and then make calls to those services to obtain additional data.

Both of these service descriptors use the DataLink standard, but only the first, the links service, involves a separate standalone service to provide the service descriptors.
Other service descriptors are attached directly to TAP search results.

.. _links-service:

Links service
=============

The Rubin Science Platform offers two mechanisms for locating images: Make a TAP query against the ObsCore_ schema, or use the SIAv2_ service.

.. _ObsCore: https://www.ivoa.net/documents/ObsCore/
.. _SIAv2: https://www.ivoa.net/documents/SIA/

In both cases, there are two supported ways of linking to the underlying image in a result row.
Either the URL for the image can be provided directly in the ``access_url`` column, or the ``access_url`` column can contain a link to the DataLink links service for the corresponding image ID.
These two cases are distinguished by the value of the ``access_format`` column, which is set to ``application/x-votable+xml;content=datalink`` if ``access_url`` is a link to a DataLink service.

The Rubin Science Platform always uses the latter approach of providing a link to a DataLink service rather than a direct link to the image.
This simplifies the data stored in the database underlying the TAP service, provides an opportunity for access control checks and internal authorization, and allows the DataLink service to provide additional service descriptors alongside the link to the image, such as a service descriptor for a cutout service that the client can use if only a portion of the image is needed.

Rubin Observatory data sets are restricted to data rights holders by default, so the image links require authentication.
Given other architectural design constraints of the Rubin Science Platform at the :abbr:`IDF (Interim Data Facility)`, the easiest way to provide an authenticated link to the image is a pre-authenticated link to the underlying storage system with a short expiration.
Those links are only provided in response to an authenticated query, which allows the service that provides the link to perform all necessary access control checks and allows the underlying image storage to trust the signed URL and not have to know about user identities, groups, or other authorization complexity.

Since these links by necessity change constantly, they cannot be put directly into the database tables underlying the ObsCore schema.
Using a DataLink links service URL allows storing static links in the underlying database table while still authenticating access to images.

Architecture
------------

In the Rubin Science Platform design, the creation of all signed URLs is handled by a separate service called the Butler_.
See :dmtn:`169` for more details.
The DataLink service queries the Butler on behalf of the user for the signed URL and then returns that URL wrapped in a DataLink record.

.. _Butler: https://arxiv.org/abs/2206.14941

In addition to providing a link to the image, we also want to provide pointers to associated services, most notably a SODA_ image cutout service.
There are two basic ways to do this: return the service descriptor directly with the VOTable provided by the TAP and SIAv2 services, or use a pointer to an external DataLink links service and attach the service. 
Since we are using a DataLink links service anyway, attaching the service descriptor there is simpler and keeps all the metadata about the image together.

.. _SODA: https://www.ivoa.net/documents/SODA/

The DataLink service discovers the location of any other services, such as SODA cutout services, via Repertoire_, the service discovery system for the Rubin Science Platform.
See :dmtn:`250` for more details.

.. _Repertoire: https://repertoire.lsst.io/

Here is the architecture in diagram form:

.. diagrams:: architecture.py

This architecture is agnostic about the underlying storage.
Any storage mechanism that supports pre-signed URLs that can be used directly by clients can be used.

The authorization flow in this diagram is somewhat simplified for clarity.
Technically, the Kubernetes ingress asks the authorization service for verification and then sends the request directly to the service, but conceptually authorization sits in front of the service as is shown here.

Here is an interaction diagram of the flow involved in retrieving a specific image:

.. mermaid:: ./flow.mmd
   :caption: Image retrieval sequence

The authentication and authorization check in front of every Science Platform request has been omitted for clarity.
See :dmtn:`234` for details on authentication and authorization.

Implementation
--------------

In the database underlying the TAP service, the ``access_url`` column for each observation with a corresponding retrievable image is set to ``<base-url>/api/datalink/links?ID=<id>`` where ``<base-url>`` is some arbitrary base URL and ``<id>`` is a unique identifier for that image in Butler_.
When returning the results of TAP queries that include the ``access_url`` column, the Qserv Kafka bridge rewrites the URLs to their appropriate value for the DataLink service in the local environment.
See :sqr:`097` for more details about the TAP architecture.

To indicate that this is a URL to a DataLink service, the ``access_format`` column is set to ``application/x-votable+xml;content=datalink``.

We also add a service descriptor to the TAP result indicating that ``access_url`` is a link to a DataLink links service.

The DataLink service URL is handled by a separate service called datalinker_.
The provided ID is opaque to datalinker; it only passes it to the Butler and asks for the corresponding signed URL.
Butler creates a signed URL that is valid for one hour.
datalinker then generates a DataLink links response, including that URL and any other relevant service descriptors for the local environment, such as ones for the cutout service if one is available.

Because the return from the DataLink links service grants direct access to an image, this route requires ``read:image`` scope.
Note that this usefully separates TAP query permissions from image access permissions: the ObsTap query to get metadata requires ``read:tap`` scope, but the user then needs ``read:image`` scope as well to get the underlying image.
See :dmtn:`235` for more details about authorization scopes.

Service descriptors
===================

The second place we use the IVOA DataLink standard is to provide easy access to additional queries related to the results of a TAP query.
This information is entirely unrelated to the DataLink links service.
It uses the same XML schema, but the service descriptors are attached directly by the TAP server to the VOTable containing TAP search results.
The service descriptors describe how to use parameters from either a row of the query results or ones provided by a user to query another service.

Confusingly, these supplemental services pointed to by the DataLink record are often currently provided by the datalinker_ service.
This was an arbitrary choice of convenience; these services are entirely unrelated to the DataLink links service.
In the future, these services will be moved to another code base and another Phalanx application.

Currently, these DataLink service descriptors are primarily used to provide easy access to related TAP queries that can be used to further explore a row of the results.
For example, one of the additional queries we provide retrieves a light curve for an object.
We attach that service descriptor to any results that include the ``objectId`` column of the ``Object`` table.
The access URL of the DataLink entry currently points to ``<rsp-base-url>/api/datalink/timeseries``.
This URL takes the following parameters:

#. ``id`` is set to the value of ``objectId``
#. ``table`` (the table containing time series data) is set to the ``ForcedSource`` table
#. ``id_column`` (the foreign key in that table) is set to ``objectId``
#. ``join_time_column`` (the column containing the observation time) is set to the ``expMidptMJD`` column in the ``CcdVisit`` table
#. ``band`` is up to the user to choose from the values ``all`` or any one of ``ugrizy``
#. ``detail`` is up to the user to choose from ``full``, ``principal``, or ``minimal`` (discussed further in :ref:`datalinker-service`)

This is just an example; the details are specific to this one schema and not horribly important.
The general principle is that any result column can be linked to a service, specifying some mix of fixed parameters (based on the result row or on known details of the table to which the DataLink service descriptor is attached) and user-chosen parameters.
The Portal Aspect and other IVOA clients can then prompt the user for the required parameters and construct an access URL for that service.

When that URL is visited, the effect is to execute a new TAP query and return the result as a new VOTable.
That VOTable can, in turn, have new service descriptors, allowing the user to follow these service prompts to explore the data further if they choose.

Making this all work involves several moving parts.

TAP configuration
-----------------

Each of these DataLink service descriptors is defined in an XML file in the `sdm_schemas repository <https://github.com/lsst/sdm_schemas/tree/main/datalink>`__.
This is a verbatim copy of the service descriptor to return, apart from an ``<INFO>`` tag that describes the column to which the service descriptor is attached.

.. code-block:: xml

   <INFO name="$dp02_dc2_catalogs_Object_objectId$"
         ID="$dp02_dc2_catalogs_Object_objectId$"
         value="this will be dropped..." />

A parameter in the resulting DataLink service descriptor will then look like:

.. code-block:: xml

   <PARAM name="id" datatype="long" ref="$dp02_dc2_catalogs_Object_objectId$"
          value="" ucd="meta.id">

In the DataLink service descriptor returned to the client, that ``ref`` value will be replaced with a reference to the corresponding column in the result VOTable.

All DataLink service descriptors are described in a JSON manifest file named ``datalink-manifest.json``.
This is a JSON object, the keys of which are the names of the XML files found in the same directory that define the DataLink service descriptor, and the values are lists of columns to which that service descriptor applies.
Periods in the columns are replaced with underscores.
For example, a manifest containing only an entry for the DataLink service descriptor described above would look like:

.. code-block:: json

   {
       "dp02_object_to_fs_timeseries": ["dp02_dc2_catalogs_Object_objectId"]
   }

Each time a release is made from the `sdm_schemas repository`_, a GitHub release artifact is created (via GitHub Actions) named ``datalink-snippets.zip``.
The TAP service is then configured (using ``datalinkPayloadUrl`` in its `values.yaml file in Phalanx <https://github.com/lsst-sqre/phalanx/blob/main/applications/tap/values.yaml>`__) to point to that artifact.

.. _sdm_schemas repository: https://github.com/lsst/sdm_schemas

When the TAP service is restarted, that URL is retrieved and unpacked.
The Rubin Science Platform TAP service then reads that directory and uses it to annotate results.

.. _datalinker-service:

TAP microsservice configuration
-------------------------------

The implementation of this service on the datalinker side is conceptually simple.
It uses the parameters to construct a TAP query, and then returns a redirect to the TAP service sync API to perform that query.
Because this service is effectively a front end to TAP queries, it is protected by the ``read:tap`` scope.

The main complexity comes from the ``detail`` parameter.
The purpose of ``detail`` is to allow the user to decide what columns should be included in the query result.
The three valid values are:

full
    Include all columns of the table being queried.

principal
    Include only the principal (most generally useful) columns.
    These are the columns for which the ``principal`` flag is true in the corresponding TAP schema entry.

minimal [#]_
    Include only the minimal columns required to follow service descriptor links to other tables of interest.
    This is intended for use in data exploration, where the next link in the exploration isn't the final destination but is only being followed to reach other tables of interest.

In order to correctly respond to these requests for a ``detail`` parameter other than ``full``, the datalinker service has to be able to look up the list of columns to include for a given table.

For ``principal``, since this is the same as the principal columns in the TAP schema, the canonical source of this data is the underlying schema.
For Rubin Observatory, the schema definitions live in the `sdm_schemas repository`_, and are maintained by a software package called Felis_.
The principal columns are tagged with ``tap:principal: 1`` in the Felis definition.

.. _Felis: https://felis.lsst.io/

Therefore, to provide this information to the datalinker service, the same GitHub Actions build process that generates ``datalink-snippets.zip`` above also generates the artifact ``datalink-columns.zip``.
This ZIP archive contains two files, ``columns-minimal.json`` and ``columns-principal.json``, which list the minimal and principal columns for each table.

``columns-principal.json`` is generated automatically from the Felis schema, and the columns are ordered according to the order defined by the ``tap:column_index`` attributes of each column.

There is not yet a Felis attribute for marking minimal columns, so for the time being the ``columns-minimal.json`` file is hand-maintained.

As noted above, these TAP microservices are conceptually unrelated to the DataLink links service and will eventually be moved to a different code base and Phalanx application.

.. [#] Neither the name nor the definition of minimal columns has been finalized.
       We may change this parameter based on further experience with what sorts of queries users want to perform.

Future work
===========

- Currently, each time there is a new release of the schemas, the Phalanx configuration of both datalinker and TAP have to be updated to pick up the new versions of the release artifacts they use for configuration.
  This creates an unwanted opportunity for version mismatches between components of a given Science Platform deployment.
  In the future, rather than use the current mechanism of directly retrieving artifacts from GitHub, we plan to serve these artifacts from Repertoire_.
  TAP and the provider of the TAP microservices will then query Repertoire for the necessary metadata.
  This way, the data returned by the service will always match the deployed version of the TAP schema, which is also managed by Repertoire.

- The ``minimal`` value of the ``detail`` parameter is currently not fully defined and hasn't been tested against what types of data explorations users may wish to perform.
