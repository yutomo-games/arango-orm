"""
A wrapper around python-arango's database class adding some SQLAlchemy like ORM methods to it.
"""
import logging
from inspect import isclass

from arango.database import Database as ArangoDatabase
from .collections import CollectionBase

log = logging.getLogger(__name__)


class Query(object):
    """
    Class used for querying records from an arangodb collection using a database connection
    """

    def __init__(self, CollectionClass, db=None):

        self._db = db
        self._CollectionClass = CollectionClass
        self._bind_vars = {'@collection': self._CollectionClass.__collection__}
        self._filter_conditions = []
        self._sort_columns = []
        self._limit = None
        self._limit_start_record = 0

    def count(self):
        "Return collection count"

        # return self._db.collection(self._CollectionClass.__collection__).count()
        aql = self._make_aql()

        aql += '\n COLLECT WITH COUNT INTO rec_count RETURN rec_count'
        # print(aql)

        results = self._db.aql.execute(aql, bind_vars=self._bind_vars)

        return next(results)

    def by_key(self, key, **kwargs):
        "Return a single document using it's key"

        doc_dict = self._db.collection(self._CollectionClass.__collection__).get(key, **kwargs)
        if doc_dict is None:
            return None

        return self._CollectionClass._load(doc_dict)

    def filter(self, condition, _or=False, prepend_rec_name=True, **kwargs):
        """
        Filter the results based on given condition. By default filter conditions are joined
        by AND operator if this method is called multiple times. If you want to use the OR operator
        then specify _or=True
        """

        joiner = None
        if len(self._filter_conditions) > 0:
            joiner = 'OR' if _or else 'AND'

        self._filter_conditions.append(dict(condition=condition, joiner=joiner,
                                            prepend_rec_name=prepend_rec_name))
        self._bind_vars.update(kwargs)

        return self

    def sort(self, col_name):
        "Add a sort condition, sorting order of ASC or DESC can be provided after col_name and a space"

        self._sort_columns.append(col_name)

        return self

    def limit(self, num_records, start_from=0):

        assert isinstance(num_records, int)
        assert isinstance(start_from, int)

        self._limit = num_records
        self._limit_start_record = start_from

        return self

    def _make_aql(self):
        "Make AQL statement from filter, sort and limit expressions"

        # Order => FILTER, SORT, LIMIT
        aql = 'FOR rec IN @@collection\n'

        # Process filter conditions

        for fc in self._filter_conditions:
            line = ""
            if fc['joiner'] is None:
                line = "FILTER "
            else:
                line = fc['joiner'] + ' '

            if fc['prepend_rec_name']:
                line += 'rec.'

            line += fc['condition']

            aql += line + ' '

        # Process Sort
        if self._sort_columns:
            aql += '\n SORT'

            for sc in self._sort_columns:
                aql += ' rec.' + sc + ','

            aql = aql[:-1]

        # Process Limit
        if self._limit:
            aql += "\n LIMIT {}, {} ".format(self._limit_start_record, self._limit)

        log.debug(aql)

        return aql

    def update(self, wait_for_sync=True, ignore_errors=False, **kwargs):

        options = " OPTIONS {waitForSync: %s, ignoreErrors: %s}" % \
                  (str(wait_for_sync).lower(), str(ignore_errors).lower())

        aql = self._make_aql()

        # Since we might already have the normal field names in self._bind_vars from filter
        # condition(s) we'll store the update variables with a different prefix "_up_" to avoid
        # field name collisions

        update_clause = ''

        # we'll need to marshal the update values using marshmallow so we create a new collection
        # object with kwargs
        up_obj = self._CollectionClass(**kwargs)

        for k, v in kwargs.items():
            update_clause += '{k}: @_up_{k},'.format(k=k)
            self._bind_vars['_up_' + k] = up_obj._dump(only=(k, ))[k]

        if len(update_clause) > 0:
            update_clause = update_clause[:-1]

        aql += "\n UPDATE {_key: rec._key} WITH {%s} IN @@collection" % update_clause + options
        log.critical(aql)
        log.critical(self._bind_vars)

        return self._db.aql.execute(aql, bind_vars=self._bind_vars)

    def delete(self, wait_for_sync=True, ignore_errors=False):

        options = " OPTIONS {waitForSync: %s, ignoreErrors: %s}" % \
                  (str(wait_for_sync).lower(), str(ignore_errors).lower())

        aql = self._make_aql()

        aql += "\n REMOVE {_key: rec._key} IN @@collection" + options

        return self._db.aql.execute(aql, bind_vars=self._bind_vars)

    def all(self):
        "Return all records considering current filter conditions (if any)"

        aql = self._make_aql()

        aql += '\n RETURN rec'
        # print(aql)

        results = self._db.aql.execute(aql, bind_vars=self._bind_vars)
        ret = []

        for rec in results:
            ret.append(self._CollectionClass._load(rec))

        return ret

    def aql(self, query, **kwargs):
        """
        Return results based on given AQL query. bind_vars already contains @@collection param.
        Query should always refer to the current collection using @collection
        """

        if 'bind_vars' in kwargs:
            kwargs['bind_vars']['@collection'] = self._bind_vars['@collection']
        else:
            kwargs['bind_vars'] = {'@collection': self._bind_vars['@collection']}

        return [self._CollectionClass._load(rec) for rec in self._db.aql.execute(query, **kwargs)]
