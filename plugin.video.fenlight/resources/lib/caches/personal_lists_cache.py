from caches.base_cache import connect_database, get_timestamp
from modules.settings import watch_history_profile_name, watched_indicators
from modules.kodi_utils import logger

def get_database(watched_indicator=None):
	conn_db = connect_database({0: 'personal_lists_db', 1: 'trakt_db', 2: 'mariadb'}[watched_indicator])
	if conn_db: return conn_db
	else: raise Exception('Failed to connect to database.')

class PersonalListsCache:
	def make_list(self, list_name, sort_order):
		dbcon = get_database(watched_indicators())
		if watched_indicators() == 2:
			try:
				dbcon.execute('INSERT INTO personal_lists (name, contents, total, created, sort_order, profile) VALUES (?, ?, ?, ?, ?, ?)', (list_name, repr([]), 0, get_timestamp(), sort_order, watch_history_profile_name()))
				return True
			except Exception as e:
				logger('personal_lists_cache MariaDB', severity='high', error_message=str(e))
				return False
		else:
			try:
				dbcon.execute('INSERT OR REPLACE INTO personal_lists VALUES (?, ?, ?, ?, ?)', (list_name, repr([]), 0, get_timestamp(), sort_order))
				return True
			except: return False

	def delete_list(self, list_name):
		dbcon = get_database(watched_indicators())
		if watched_indicators() == 2:
			try:
				dbcon.execute('DELETE FROM personal_lists WHERE name=? and profile=?', (list_name, watch_history_profile_name()))
				dbcon.execute('OPTIMIZE TABLE personal_lists')
				return True
			except Exception as e: 
				logger('personal_lists_cache MariaDB', severity='high', error_message=str(e))
				return False
		else:
			try:
				dbcon.execute('DELETE FROM personal_lists WHERE name=?', (list_name,))
				dbcon.execute('VACUUM')
				return True
			except: return False

	def delete_list_contents(self, list_name):
		dbcon = get_database(watched_indicators())
		if watched_indicators() == 2:
			try:
				dbcon.execute('UPDATE personal_lists SET contents=?, total=? WHERE name=? and profile=?', (repr([]), '0', list_name, watch_history_profile_name()))
				return True
			except Exception as e: 
				logger('personal_lists_cache MariaDB', severity='high', error_message=str(e))
				return False
		else:
			try:
				dbcon.execute('UPDATE personal_lists SET contents=?, total=? WHERE name=?', (repr([]), '0', list_name))
				return True
			except: return False

	def update_list_details(self, list_name, sort_order, original_name):
		dbcon = get_database(watched_indicators())
		if watched_indicators() == 2:
			try:
				dbcon.execute('UPDATE personal_lists SET name=?, sort_order=? WHERE name=? and profile=?', (list_name, sort_order, original_name, watch_history_profile_name()))
				return True
			except Exception as e: 
				logger('personal_lists_cache MariaDB', severity='high', error_message=str(e))
				return False
		else:
			try:
				dbcon.execute('UPDATE personal_lists SET name=?, sort_order=? WHERE name=?', (list_name, sort_order, original_name))
				return True
			except: return False

	def get_lists(self):
		dbcon = get_database(watched_indicators())
		if watched_indicators() == 2:
			try:
				all_lists = dbcon.execute('SELECT name, total, sort_order FROM personal_lists WHERE profile=?', (watch_history_profile_name(),))
				return [{'name': str(i[0]), 'total': i[1], 'sort_order': i[2]} for i in all_lists]
			except Exception as e: 
				logger('personal_lists_cache MariaDB', severity='medium', error_message=str(e))
				return []
		if watched_indicators() == 3:
			try:
				all_lists = dbcon.get_lists()
				return all_lists
			except Exception as e: 
				logger('personal_lists_cache MariaDB', severity='medium', error_message=str(e))
				return []
		else:
			try:
				all_lists = dbcon.execute('SELECT name, total, sort_order FROM personal_lists').fetchall()
				return [{'name': str(i[0]), 'total': i[1], 'sort_order': i[2]} for i in all_lists]
			except: return []

	def get_list(self, list_name, dbcon=None):
		dbcon = get_database(watched_indicators())
		if watched_indicators() == 2:
			try:
				return eval(dbcon.execute('SELECT contents FROM personal_lists WHERE name=? and profile=?', (list_name, watch_history_profile_name())).fetchone()[0])
			except Exception as e: 
				logger('personal_lists_cache MariaDB', severity='high', error_message=str(e))
				return []
		else:
			try:
				return eval(dbcon.execute('SELECT contents FROM personal_lists WHERE name=?', (list_name,)).fetchone()[0])
			except: return []

	def add_remove_list_item(self, action, new_contents, list_name):
		dbcon = get_database(watched_indicators())
		if watched_indicators() == 2:
			try:
				contents = self.get_list(list_name, dbcon)
				if action == 'add':
					if [str(i['media_id']) for i in contents if str(new_contents['media_id']) == str(i['media_id'])]: return 'Item Already in [B]%s[/B]' % list_name
					command = 'UPDATE personal_lists SET contents=?, total=total+1 WHERE name=? and profile=?'
					contents.append(new_contents)
				else:
					if not [str(i['media_id']) for i in contents if str(new_contents) == str(i['media_id'])]: return 'Item Not in [B]%s[/B]' % list_name
					command = 'UPDATE personal_lists SET contents=?, total=total-1 WHERE name=? and profile=?'
					contents = [i for i in contents if not str(i['media_id']) == str(new_contents)]
				dbcon.execute(command, (repr(contents), list_name, watch_history_profile_name()))
				return 'Success'
			except Exception as e: 
				logger('personal_lists_cache MariaDB', severity='high', error_message=str(e))
				return 'Error'
		else:
			try:
				contents = self.get_list(list_name, dbcon)
				if action == 'add':
					if [str(i['media_id']) for i in contents if str(new_contents['media_id']) == str(i['media_id'])]: return 'Item Already in [B]%s[/B]' % list_name
					command = 'UPDATE personal_lists SET contents=?, total=total+1 WHERE name=?'
					contents.append(new_contents)
				else:
					if not [str(i['media_id']) for i in contents if str(new_contents) == str(i['media_id'])]: return 'Item Not in [B]%s[/B]' % list_name
					command = 'UPDATE personal_lists SET contents=?, total=total-1 WHERE name=?'
					contents = [i for i in contents if not str(i['media_id']) == str(new_contents)]
				dbcon.execute(command, (repr(contents), list_name))
				return 'Success'
			except: return 'Error'

	def add_many_list_items(self, new_contents, list_name):
		dbcon = get_database(watched_indicators())
		if watched_indicators() == 2:
			try:
				contents = self.get_list(list_name, dbcon)
				compare_ids = [str(i['media_id']) for i in contents]
				new_contents = [i for i in new_contents if str(i['media_id']) not in compare_ids]
				contents.extend(new_contents)
				dbcon.execute('UPDATE personal_lists SET contents=?, total=? WHERE name=? and profile=?', (repr(contents), len(contents), list_name, watch_history_profile_name()))
				return 'Success'
			except Exception as e: 
				logger('personal_lists_cache MariaDB', severity='high', error_message=str(e))
				return 'Error'
		else:
			try:
				contents = self.get_list(list_name, dbcon)
				compare_ids = [str(i['media_id']) for i in contents]
				new_contents = [i for i in new_contents if str(i['media_id']) not in compare_ids]
				contents.extend(new_contents)
				dbcon.execute('UPDATE personal_lists SET contents=?, total=? WHERE name=?', (repr(contents), len(contents), list_name))
				return 'Success'
			except: return 'Error'

personal_lists_cache = PersonalListsCache()
