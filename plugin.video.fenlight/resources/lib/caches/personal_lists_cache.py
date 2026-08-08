from caches.base_cache import connect_database, get_timestamp
from modules.settings import watch_history_profile_name, watch_history_database_name, watched_indicators

class PersonalListsCache:
	def make_list(self, list_name, sort_order):
		if watched_indicators() == 2:
			try:
				conn = connect_database(watch_history_database_name())
				with conn.cursor() as cursor:
					cursor.execute('INSERT INTO personal_lists (name, contents, total, created, sort_order, profile) VALUES (%s, %s, %s, %s, %s, %s)', (list_name, repr([]), 0, get_timestamp(), sort_order, watch_history_profile_name()))
				return True
			except: return False
		else:
			try:
				dbcon = connect_database('personal_lists_db')
				dbcon.execute('INSERT OR REPLACE INTO personal_lists VALUES (?, ?, ?, ?, ?)', (list_name, repr([]), 0, get_timestamp(), sort_order))
				return True
			except: return False

	def delete_list(self, list_name):
		if watched_indicators() == 2:
				try:
					conn = connect_database(watch_history_database_name())
					with conn.cursor() as cursor:
						cursor.execute('DELETE FROM personal_lists WHERE name=%s and profile=%s', (list_name, watch_history_profile_name()))
						cursor.execute('OPTIMIZE TABLE personal_lists')
					return True
				except: return False
		else:
			try:
				dbcon = connect_database('personal_lists_db')
				dbcon.execute('DELETE FROM personal_lists WHERE name=?', (list_name,))
				dbcon.execute('VACUUM')
				return True
			except: return False

	def delete_list_contents(self, list_name):
		if watched_indicators() == 2:
				try:
					conn = connect_database(watch_history_database_name())
					with conn.cursor() as cursor:
						cursor.execute('UPDATE personal_lists SET contents=%s, total=%s WHERE name=%s and profile=%s', (repr([]), '0', list_name, watch_history_profile_name()))
					return True
				except: return False
		else:
			try:
				dbcon = connect_database('personal_lists_db')
				dbcon.execute('UPDATE personal_lists SET contents=?, total=? WHERE name=?', (repr([]), '0', list_name))
				return True
			except: return False

	def update_list_details(self, list_name, sort_order, original_name):
		if watched_indicators() == 2:
				try:
					conn = connect_database(watch_history_database_name())
					with conn.cursor() as cursor:
						cursor.execute('UPDATE personal_lists SET name=%s, sort_order=%s WHERE name=%s and profile=%s', (list_name, sort_order, original_name, watch_history_profile_name()))
					return True
				except: return False
		else:
			try:
				dbcon = connect_database('personal_lists_db')
				dbcon.execute('UPDATE personal_lists SET name=?, sort_order=? WHERE name=?', (list_name, sort_order, original_name))
				return True
			except: return False

	def get_lists(self):
		if watched_indicators() == 2:
				try:
					conn = connect_database(watch_history_database_name())
					with conn.cursor() as cursor:
						cursor.execute('SELECT name, total, sort_order FROM personal_lists WHERE profile=%s', (watch_history_profile_name(),))
						all_lists = cursor.fetchall()
					return [{'name': str(i[0]), 'total': i[1], 'sort_order': i[2]} for i in all_lists]
				except: return []
		else:
			try:
				dbcon = connect_database('personal_lists_db')
				all_lists = dbcon.execute('SELECT name, total, sort_order FROM personal_lists').fetchall()
				return [{'name': str(i[0]), 'total': i[1], 'sort_order': i[2]} for i in all_lists]
			except: return []

	def get_list(self, list_name, dbcon=None):
		if watched_indicators() == 2:
				try:
					conn = dbcon or connect_database(watch_history_database_name())
					with conn.cursor() as cursor:
						cursor.execute('SELECT contents FROM personal_lists WHERE name=%s and profile=%s', (list_name, watch_history_profile_name()))
						result = cursor.fetchone()
					return eval(result[0])
				except: return []
		else:
			try:
				if not dbcon: dbcon = connect_database('personal_lists_db')
				return eval(dbcon.execute('SELECT contents FROM personal_lists WHERE name=?', (list_name,)).fetchone()[0])
			except: return []

	def add_remove_list_item(self, action, new_contents, list_name):
		if watched_indicators() == 2:
				try:
					conn = connect_database(watch_history_database_name())
					contents = self.get_list(list_name, conn)
					if action == 'add':
						if [str(i['media_id']) for i in contents if str(new_contents['media_id']) == str(i['media_id'])]: return 'Item Already in [B]%s[/B]' % list_name
						command = 'UPDATE personal_lists SET contents=%s, total=total+1 WHERE name=%s and profile=%s'
						contents.append(new_contents)
					else:
						if not [str(i['media_id']) for i in contents if str(new_contents) == str(i['media_id'])]: return 'Item Not in [B]%s[/B]' % list_name
						command = 'UPDATE personal_lists SET contents=%s, total=total-1 WHERE name=%s and profile=%s'
						contents = [i for i in contents if not str(i['media_id']) == str(new_contents)]
					with conn.cursor() as cursor:
						cursor.execute(command, (repr(contents), list_name, watch_history_profile_name()))
					return 'Success'
				except: return 'Error'
		else:
			try:
				dbcon = connect_database('personal_lists_db')
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
		if watched_indicators() == 2:
				try:
					conn = connect_database(watch_history_database_name())
					contents = self.get_list(list_name, conn)
					compare_ids = [str(i['media_id']) for i in contents]
					new_contents = [i for i in new_contents if str(i['media_id']) not in compare_ids]
					contents.extend(new_contents)
					with conn.cursor() as cursor:
						cursor.execute('UPDATE personal_lists SET contents=%s, total=%s WHERE name=%s and profile=%s', (repr(contents), len(contents), list_name, watch_history_profile_name()))
					return 'Success'
				except: return 'Error'
		else:
			try:
				dbcon = connect_database('personal_lists_db')
				contents = self.get_list(list_name, dbcon)
				compare_ids = [str(i['media_id']) for i in contents]
				new_contents = [i for i in new_contents if str(i['media_id']) not in compare_ids]
				contents.extend(new_contents)
				dbcon.execute('UPDATE personal_lists SET contents=?, total=? WHERE name=?', (repr(contents), len(contents), list_name))
				return 'Success'
			except: return 'Error'

personal_lists_cache = PersonalListsCache()
