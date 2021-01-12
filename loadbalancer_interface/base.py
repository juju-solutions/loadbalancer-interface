import json
import weakref
from hashlib import md5
from operator import attrgetter

from cached_property import cached_property
from marshmallow import Schema, fields

from ops.framework import (
    Object,
)


VERSION = '1'


class Response:
    @classmethod
    def get_all(cls, relation):
        raise NotImplementedError()

    def __init__(self, relation):
        self.relation = relation

    @property
    def hash(self):
        raise NotImplementedError()


class HealthCheck:
    class _Schema(Schema):
        traffic_type = fields.Str(required=True)
        port = fields.Int(required=True)
        path = fields.Str(missing=None)
        interval = fields.Int(missing=30)
        retries = fields.Int(missing=3)

    def __init__(self, **kwargs):
        self._schema = self._Schema()
        for field, value in self._schema.load(kwargs).items():
            setattr(self, field, value)


class HealthCheckField(fields.Field):
    def _serialize(self, value, attr, obj, **kwargs):
        return value._schema.dump(value)

    def _deserialize(self, value, attr, data, **kwargs):
        if isinstance(value, HealthCheck):
            return value
        return HealthCheck(**value)


class Request:
    class _Schema(Schema):
        traffic_type = fields.Str(required=True)
        backends = fields.List(fields.Str(), missing=list)
        backend_ports = fields.List(fields.Int(), required=True)
        algorithm = fields.List(fields.Str(), missing=list)
        sticky = fields.Bool(missing=False)
        health_checks = fields.List(HealthCheckField, missing=list)
        public = fields.Bool(missing=True)
        tls_termination = fields.Bool(missing=False)
        tls_cert = fields.Str(missing=None)
        tls_key = fields.Str(missing=None)
        ingress_address = fields.Str(missing=None)
        ingress_ports = fields.List(fields.Int(), missing=list)

    @classmethod
    def get_all(cls, app, relation):
        requests = []
        for key, value in relation.data[app].items():
            if not key.startswith('request_'):
                continue
            name = key.split('_', 1)[1]
            requests.append(cls(app,
                                relation,
                                name,
                                response=Response.get(app, relation, name),
                                **json.loads(value)))
        return requests

    def __init__(self, app, relation, name, *, response=None, **kwargs):
        self._schema = self._Schema()
        self.app = app
        self.relation = relation
        self.name = name
        self.response = response
        for field, value in self._schema.load(kwargs).items():
            setattr(self, field, value)

    def dump(self):
        return self._schema.dump(self)

    def dumps(self):
        return json.dumps(self.dump(), sort_keys=True)

    @property
    def hash(self):
        return md5(self.dumps().encode('utf8')).hexdigest()

    def write(self):
        self.relation.data[self.app]['request_' + self.name] = self.dumps()


class LBBase(Object):
    def __init__(self, charm, relation_name):
        super().__init__(charm, relation_name)
        self.charm = weakref.proxy(charm)
        self.relation_name = relation_name
        self.state.set_default(hash=None)

        # Future-proof against the need to evolve the relation protocol
        # by ensuring that we agree on a version number before starting.
        # This may or may not be made moot by a future feature in Juju.
        for event in (charm.on[relation_name].relation_created,
                      charm.on.leader_elected,
                      charm.on.upgrade_charm):
            self.framework.observe(event, self._set_version)

    def _set_version(self, event):
        if self.unit.is_leader():
            if hasattr(event, 'relation'):
                relations = [event.relation]
            else:
                relations = self.model.relations.get(self.relation_name, [])
            for relation in relations:
                relation.data[self.app]['version'] = str(VERSION)

    @cached_property
    def relations(self):
        relations = self.model.relations.get(self.relation_name, [])
        return [relation
                for relation in sorted(relations, key=attrgetter('id'))
                if VERSION == relation.data.get(relation.app,
                                                {}).get('version')]

    @property
    def is_changed(self):
        return self.state.hash == self.hash

    @is_changed.setter
    def is_changed(self, value):
        if not value:
            self.state.hash = self.hash

    @property
    def model(self):
        return self.framework.model

    @property
    def app(self):
        return self.charm.app

    @property
    def unit(self):
        return self.charm.unit
