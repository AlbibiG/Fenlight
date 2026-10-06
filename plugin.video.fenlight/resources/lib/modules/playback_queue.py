import json
import sys
from datetime import date
from uuid import uuid4
from modules import kodi_utils
from modules import metadata, settings
from modules.utils import get_datetime

_QUEUE_PROPERTY = 'fenlight.playback_queue'

def get_queue():
	try:
		queue = json.loads(kodi_utils.get_property(_QUEUE_PROPERTY) or '[]')
		return queue if isinstance(queue, list) else []
	except Exception as e:
		kodi_utils.logger('playback_queue.get_queue', severity='medium', error_message=str(e))
		kodi_utils.clear_property(_QUEUE_PROPERTY)
		return []


def _save_queue(queue):
	kodi_utils.set_property(_QUEUE_PROPERTY, json.dumps(queue))


def has_items():
	return bool(get_queue())


def peek():
	queue = get_queue()
	if not queue: return None
	return queue[0]


def mark_started(queue_id):
	queue = get_queue()
	updated_queue = [item for item in queue if item.get('queue_id') != queue_id]
	if len(updated_queue) == len(queue): return
	_save_queue(updated_queue)
	#kodi_utils.notification('Removed from Queue', 2000)


def _matches_item(queue_item, media_type, tmdb_id, season=None, episode=None):
	params = queue_item.get('params', {})
	if str(params.get('tmdb_id')) != str(tmdb_id): return False
	if media_type == 'movie': return params.get('media_type') == 'movie'
	if params.get('media_type') != 'episode': return False
	if season is not None and str(params.get('season')) != str(season): return False
	if episode is not None and str(params.get('episode')) != str(episode): return False
	return True


def context_menu_action(media_type, tmdb_id, title=None, season=None, episode=None, ep_title=None):
	queue = get_queue()
	queued = any(_matches_item(item, media_type, tmdb_id, season, episode) for item in queue)
	if queued:
		params = {'mode': 'queue.remove_item', 'media_type': media_type, 'tmdb_id': tmdb_id}
		if season is not None: params['season'] = season
		if episode is not None: params['episode'] = episode
		label = '[B]Remove from Queue[/B]'
	else:
		params = {'mode': 'queue.add', 'media_type': media_type, 'tmdb_id': tmdb_id}
		if title is not None: params['title'] = title
		if season is not None: params['season'] = season
		if episode is not None: params['episode'] = episode
		if ep_title is not None: params['ep_title'] = ep_title
		label = '[B]Add to Queue[/B]'
	return label, 'RunPlugin(%s)' % kodi_utils.build_url(params)


def remove_item(params):
	media_type, tmdb_id = params.get('media_type'), params.get('tmdb_id')
	season, episode = params.get('season'), params.get('episode')
	queue = get_queue()
	updated_queue = [item for item in queue if not _matches_item(item, media_type, tmdb_id, season, episode)]
	if len(updated_queue) == len(queue): return
	_save_queue(updated_queue)
	kodi_utils.notification('Removed from Queue', 2000)
	kodi_utils.refresh_widgets()


def _entry(media_type, tmdb_id, title, season=None, episode=None, ep_title=None):
	if media_type == 'movie':
		label = title
		params = {'media_type': 'movie', 'tmdb_id': tmdb_id}
	else:
		label = '%s - S%02dE%02d' % (title, int(season), int(episode))
		if ep_title: label = '%s - %s' % (label, ep_title)
		params = {'media_type': 'episode', 'tmdb_id': tmdb_id, 'season': season, 'episode': episode}
	return {'queue_id': uuid4().hex, 'label': label, 'params': params}


def _aired_episode_entries(meta, season_numbers=None):
	title, tmdb_id = meta.get('title'), meta.get('tmdb_id')
	current_date = get_datetime()
	entries = []
	seasons = meta.get('season_data') or []
	if season_numbers is not None:
		seasons = [item for item in seasons if item.get('season_number') in season_numbers]
	for season_data in seasons:
		season = season_data.get('season_number')
		if season is None: continue
		for episode_data in metadata.episodes_meta(season, meta):
			try:
				airdate = date.fromisoformat(episode_data['premiered'])
				if airdate > current_date: continue
				entries.append(_entry('episode', tmdb_id, title, episode_data['season'], episode_data['episode'], episode_data.get('title')))
			except (KeyError, TypeError, ValueError):
				continue
	return entries


def _tvshow_meta(tmdb_id):
	return metadata.tvshow_meta('tmdb_id', tmdb_id, settings.tmdb_api_key(), settings.mpaa_region(), get_datetime())


def _trakt_meta(media_type, media_ids):
	meta_function = metadata.movie_meta if media_type == 'movie' else metadata.tvshow_meta
	return meta_function('trakt_dict', media_ids, settings.tmdb_api_key(), settings.mpaa_region(), get_datetime())


def _show_entries(tmdb_id):
	meta = _tvshow_meta(tmdb_id)
	if not meta or meta.get('blank_entry'): return []
	return _aired_episode_entries(meta)


def _season_entries(tmdb_id, season):
	meta = _tvshow_meta(tmdb_id)
	if not meta or meta.get('blank_entry'): return []
	return _aired_episode_entries(meta, {int(season)})


def _items_to_entries(items, source):
	entries = []
	for item in items:
		if source == 'trakt':
			item_type = item.get('type')
			media_ids = item.get('media_ids') or {}
			if item_type == 'movie':
				meta = _trakt_meta('movie', media_ids)
				if meta and not meta.get('blank_entry'):
					entries.append(_entry('movie', meta['tmdb_id'], item.get('title') or meta.get('title')))
			elif item_type == 'show':
				meta = _trakt_meta('tvshow', media_ids)
				if meta and not meta.get('blank_entry'): entries.extend(_aired_episode_entries(meta))
			elif item_type == 'season':
				tmdb_id, season = item.get('tmdb_id'), item.get('season')
				if tmdb_id is not None and season is not None: entries.extend(_season_entries(tmdb_id, season))
			elif item_type == 'episode':
				season, episode = item.get('season'), item.get('episode')
				meta = _trakt_meta('tvshow', media_ids)
				if meta and not meta.get('blank_entry') and season is not None and episode is not None:
					entries.append(_entry('episode', meta['tmdb_id'], item.get('title') or meta.get('title'), season, episode))
		elif source == 'personal':
			media_type, tmdb_id = item.get('type'), item.get('media_id')
			if media_type == 'movie' and tmdb_id:
				entries.append(_entry('movie', tmdb_id, item.get('title') or 'Movie'))
			elif media_type == 'tvshow' and tmdb_id:
				entries.extend(_show_entries(tmdb_id))
		elif source == 'tmdb':
			media_type, tmdb_id = item.get('media_type'), item.get('id')
			if media_type == 'movie' and tmdb_id:
				entries.append(_entry('movie', tmdb_id, item.get('title') or item.get('original_title') or 'Movie'))
			elif media_type == 'tv' and tmdb_id:
				entries.extend(_show_entries(tmdb_id))
	return entries


def add(params):
	media_type = params.get('media_type')
	tmdb_id = params.get('tmdb_id')
	entries = []
	if media_type == 'movie' and tmdb_id:
		entries.append(_entry('movie', tmdb_id, params.get('title') or 'Movie'))
	elif media_type == 'episode' and tmdb_id and params.get('season') is not None and params.get('episode') is not None:
		entries.append(_entry('episode', tmdb_id, params.get('title') or 'TV Show', params['season'], params['episode'], params.get('ep_title')))
	elif media_type == 'season' and tmdb_id and params.get('season') is not None:
		entries.extend(_season_entries(tmdb_id, params['season']))
	elif media_type == 'tvshow' and tmdb_id:
		entries.extend(_show_entries(tmdb_id))
	else:
		kodi_utils.notification('Unable to Add Item to Queue', 3000)
		return
	if not entries:
		kodi_utils.notification('No Aired Episodes to Add', 3000)
		return
	queue = get_queue()
	queue.extend(entries)
	_save_queue(queue)
	kodi_utils.notification('Added %d Item%s to Queue' % (len(entries), '' if len(entries) == 1 else 's'), 3000)
	kodi_utils.refresh_widgets()


def add_list(params):
	source = params.get('source')
	try:
		if source == 'trakt':
			from apis.trakt_api import get_trakt_list_contents
			list_type, user, slug = params['list_type'], params['user'], params['slug']
			items = get_trakt_list_contents(list_type, user, slug, list_type == 'my_lists')
		elif source == 'personal':
			from indexers.personal_lists import get_personal_list
			items = get_personal_list({'list_name': params['list_name'], 'sort_order': params.get('sort_order', '0')})
		elif source == 'tmdb':
			from indexers.tmdb_lists import get_tmdb_list
			items = get_tmdb_list(params['list_id'])
		else:
			kodi_utils.notification('Unsupported List Type', 3000)
			return
		entries = _items_to_entries(items or [], source)
	except Exception as e:
		kodi_utils.logger('playback_queue.add_list', severity='medium', error_message=str(e))
		kodi_utils.notification('Unable to Add List to Queue', 3000)
		return
	if not entries:
		kodi_utils.notification('No Playable Items Found', 3000)
		return
	queue = get_queue()
	queue.extend(entries)
	_save_queue(queue)
	kodi_utils.notification('Added %d Item%s to Queue' % (len(entries), '' if len(entries) == 1 else 's'), 3000)


def remove(params):
	queue_id = params.get('queue_id')
	queue = get_queue()
	updated_queue = [item for item in queue if item.get('queue_id') != queue_id]
	if len(updated_queue) == len(queue): return
	_save_queue(updated_queue)
	kodi_utils.notification('Removed from Queue', 2000)
	kodi_utils.refresh_widgets()


def clear():
	if not get_queue(): return
	if not kodi_utils.confirm_dialog(heading='Playback Queue', text='Clear the entire queue?'): return
	_save_queue([])
	kodi_utils.notification('Queue Cleared', 2000)
	kodi_utils.refresh_widgets()


def move(params):
	queue = get_queue()
	queue_id, direction = params.get('queue_id'), params.get('direction')
	index = next((i for i, item in enumerate(queue) if item.get('queue_id') == queue_id), None)
	if index is None: return
	new_index = index - 1 if direction == 'up' else index + 1
	if not 0 <= new_index < len(queue): return
	queue[index], queue[new_index] = queue[new_index], queue[index]
	_save_queue(queue)
	kodi_utils.refresh_widgets()


def _item_art(params, cache):
	default_art = kodi_utils.get_icon('player')
	fanart = kodi_utils.get_addon_fanart()
	art = {'icon': default_art, 'poster': default_art, 'thumb': default_art, 'fanart': fanart}
	try:
		tmdb_id, media_type = params.get('tmdb_id'), params.get('media_type')
		meta_key = (media_type == 'movie', tmdb_id)
		if meta_key not in cache:
			if media_type == 'movie':
				cache[meta_key] = metadata.movie_meta('tmdb_id', tmdb_id, settings.tmdb_api_key(), settings.mpaa_region(), get_datetime())
			else: cache[meta_key] = _tvshow_meta(tmdb_id)
		meta = cache[meta_key]
		if not meta or meta.get('blank_entry'): return art
		art['fanart'] = meta.get('fanart') or fanart
		if media_type == 'movie':
			image = meta.get('landscape') or meta.get('poster') or meta.get('fanart')
		else:
			image = None
			episodes_key = ('episodes', tmdb_id, str(params.get('season')))
			if episodes_key not in cache: cache[episodes_key] = metadata.episodes_meta(int(params.get('season')), meta)
			for episode_data in cache[episodes_key] or []:
				if str(episode_data.get('episode')) == str(params.get('episode')):
					image = episode_data.get('thumb')
					break
			image = image or meta.get('landscape') or meta.get('fanart')
		if image: art.update({'icon': image, 'poster': image, 'thumb': image})
	except Exception as e:
		kodi_utils.logger('playback_queue._item_art', severity='low', error_message=str(e))
	return art


def open():
	handle = int(sys.argv[1])
	queue = get_queue()
	build_url, make_listitem = kodi_utils.build_url, kodi_utils.make_listitem
	items = []
	art_cache = {}
	clear_url = build_url({'mode': 'queue.clear'})
	clear_item = make_listitem()
	clear_item.setLabel('[B]Clear Queue[/B]')
	clear_item.addContextMenuItems([('[B]Clear Queue[/B]', 'RunPlugin(%s)' % clear_url)])
	clear_item.setProperty('IsPlayable', 'false')
	items.append((clear_url, clear_item, False))
	for index, item in enumerate(queue):
		queue_id = item['queue_id']
		label = item.get('label', 'Queued Item')
		if index == 0: label = '[B][FF0000][UP NEXT][/FF0000][/B] %s' % label
		listitem = make_listitem()
		listitem.setLabel(label)
		listitem.setArt(_item_art(item['params'], art_cache))
		actions = [
			('[B]Remove from Queue[/B]', 'RunPlugin(%s)' % build_url({'mode': 'queue.remove', 'queue_id': queue_id})),
			('[B]Move Up[/B]', 'RunPlugin(%s)' % build_url({'mode': 'queue.move', 'queue_id': queue_id, 'direction': 'up'})),
			('[B]Move Down[/B]', 'RunPlugin(%s)' % build_url({'mode': 'queue.move', 'queue_id': queue_id, 'direction': 'down'})),
			('[B]Clear Queue[/B]', 'RunPlugin(%s)' % clear_url)
		]
		listitem.addContextMenuItems(actions)
		play_params = dict(item['params'], mode='playback.media', queue_id=queue_id)
		items.append((build_url(play_params), listitem, False))
	if not queue:
		empty = make_listitem()
		empty.setLabel('Queue is empty')
		empty.setProperty('IsPlayable', 'false')
		items.append((clear_url, empty, False))
	kodi_utils.add_items(handle, items)
	kodi_utils.set_content(handle, 'videos')
	kodi_utils.set_category(handle, 'Playback Queue')
	kodi_utils.end_directory(handle, cacheToDisc=False)
