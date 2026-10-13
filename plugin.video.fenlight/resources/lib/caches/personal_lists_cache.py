from caches.base_cache import connect_database
from modules.settings import watched_indicators
from modules.kodi_utils import logger

def get_database(watched_indicator=None):
	conn_db = connect_database({0: 'personal_lists_db', 1: 'trakt_db', 2: 'mariadb', 3: 'mariadb_api'}[watched_indicator])
	if conn_db: return conn_db
	else: raise Exception('Failed to connect to database.')

class PersonalListsCache:
	def make_list(self, list_name, sort_order):
		try:
			get_database(watched_indicators()).make_list(list_name, sort_order)
			return True
		except Exception as e:
			logger('personal_lists_cache make_list', severity='medium', error_message=str(e))
			return False

	def delete_list(self, list_name):
		try:
			get_database(watched_indicators()).delete_list(list_name)
			return True
		except Exception as e:
			logger('personal_lists_cache delete_list', severity='medium', error_message=str(e))
			return False

	def delete_list_contents(self, list_name):
		try:
			get_database(watched_indicators()).clear_list_contents(list_name)
			return True
		except Exception as e:
			logger('personal_lists_cache delete_list_contents', severity='medium', error_message=str(e))
			return False

	def update_list_details(self, list_name, sort_order, original_name):
		try:
			get_database(watched_indicators()).update_list_details(list_name, sort_order, original_name)
			return True
		except Exception as e:
			logger('personal_lists_cache update_list_details', severity='medium', error_message=str(e))
			return False

	def get_lists(self):
		try:
			all_lists = get_database(watched_indicators()).get_lists()
		except Exception as e:
			logger('personal_lists_cache get_lists', severity='medium', error_message=str(e))
			return []
		return [{'name': str(i[0]), 'total': i[1], 'sort_order': i[2]} for i in all_lists]

	def get_list(self, list_name, dbcon=None):
		try:
			return (dbcon or get_database(watched_indicators())).get_list(list_name)
		except Exception as e:
			logger('personal_lists_cache get_list', severity='medium', error_message=str(e))
			return []

	def add_remove_list_item(self, action, new_contents, list_name):
		try:
			dbcon = get_database(watched_indicators())
			if action == 'add':
				if not dbcon.add_list_item(list_name, new_contents): return 'Item Already in [B]%s[/B]' % list_name
			else:
				if not dbcon.remove_list_item(list_name, new_contents): return 'Item Not in [B]%s[/B]' % list_name
			return 'Success'
		except Exception as e:
			logger('personal_lists_cache', severity='medium', error_message=str(e))
			return 'Error'

	def add_many_list_items(self, new_contents, list_name):
		try:
			get_database(watched_indicators()).add_many_list_items(list_name, new_contents)
			return 'Success'
		except Exception as e:
			logger('personal_lists_cache add_many_list_items', severity='medium', error_message=str(e))
			return 'Error'

personal_lists_cache = PersonalListsCache()
