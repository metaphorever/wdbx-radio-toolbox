<?php
//
$capi_host = "https://confessor.wdbx.org";
$archive_host = "https://archive.wdbx.org";
//
$conf_logdir = '';
$my_log_file_name = "capi.out";
$now = time();

if(!defined("confessor_log_stuff"))
	define("confessor_log_stuff",1);

$confd = 0;

if(confessor_log_stuff)
{
	$confd = fopen($my_log_file_name,"w");
	if($confd)
	{
		fwrite($confd,"-------------------------- " . date("r") . " --------------------\n");
		@chmod($my_log_file_name,0666);
	}
}
if(!defined("confd"))
	define("confd",$confd);

if(confessor_log_stuff)
{
	error_reporting(E_ALL);
	ini_set("display_errors",1);
}
else
{
	error_reporting(E_ALL);
	ini_set("display_errors",0);
}
/*====================================================
 * all returns from this api are arrays
 *====================================================*/

/*====================================================
 * confessor_get_all_shows(no param)  returns:
 * 
 * array(<day num (0-6)> => <0-n>(shows in time order) =>
 * [sh_id] => 104
 * [sh_altid] => ftv
 * [sh_name] => From The Vault
 * [sh_desc] =>
 * [sh_url] => http://pacificaradioarchives.org
 * [sh_photo] =>
 * [sh_djname] => Pacifica Radio Archives
 * [sh_email] =>
 * [starts] => 3:00 AM
 * [sh_shour] => 10800
 * [ends] => 4:00 AM
 * [sh_len] => 3600
 * [day] => Sunday
 * [days] => Sun
 * [sh_info] => 16385
 * [sh_start_time] => 1365926400
 * )
 *====================================================*/
function confessor_get_all_shows($json_flag = 0)
{
	global $capi_host;

	$buf = '';
	$buf = file_get_contents("$capi_host/_do_api.php?req=getshows" . ($json_flag ? "&json=1" : '') );
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}

function confessor_get_gone_shows($json_flag = 0)
{
	global $capi_host;

	$buf = '';
	$buf = file_get_contents("$capi_host/_do_api.php?req=getgone". ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}

/*====================================================
 * confessor_get_show_by_time(<timestamp> or no param)
 * gets a show by time:
 * if the single param is not used it gets the current show
 * if the single param is a timestamp, it gets the show that fits the time.

 * build a specific timestamp with mktime: 
 * (timestamp = mktime(hours,minutes,seconds,month,day,year);
 *
 * so:
 * $time_stamp = mktime(<hour>,<minute>,0,<month (1-12)>,<day (1-31)>,<year (2012, 2013, etc)>);
 * note: seconds=0
 *
 * or:
 * $time_stamp = mktime(0,0,<seconds as in sh_shour>,<month><day><year>);
 * 
 * returns:
 * array(
 * [sh_id] => 24
 * [sh_name] => Democracy Now! PM
 * [sh_altid] => dnpm
 * [sh_photo] => democracy_now_24.jpg
 * [sh_djname] => Amy Goodman
 * [sh_facebook] => http://pacificaradioarchives.org
 * [sh_twitter] => http://pacificaradioarchives.org
 * [sh_tumblr] => http://pacificaradioarchives.org
 * [sh_info] => 16446
 * [sh_shour] => 61200
 * [sh_stime] => 17:00:00
 * [sh_ampm] => 5:00 PM
 * [sh_days] => Mon Tue Wed Thu Fri
 * [sh_big_days] => Monday Tuesday Wednesday Thursday Friday
 * [sh_ends] => 18:00:00
 * [sh_ampm_ends] => 6:00 PM
 * [sh_len] => 3600
 * [sh_url] =>
 * [sh_start_time] => 1365199200
 * )
 *====================================================*/
function confessor_get_show_by_time($tim = 0,$json_flag = 0)
{
	global $capi_host;

	if(empty($tim))
		$tim = time();
	$buf = file_get_contents("$capi_host/_do_api.php?req=getshow&dte=" . $tim . ($json_flag ? "&json=1" : ''));
	if(confd)
		fwrite(confd, __LINE__ . ": buf=$buf\n");
	$ary = unserialize(base64_decode($buf));
	if(confd)
		fwrite(confd, __LINE__ . ": " . print_r($ary,true) . "\n");

	return($ary);
}

function confessor_get_next_show_by_time($tim = 0,$json_flag = 0)
{
	global $capi_host;

	if(empty($tim))
		$tim = time();
	$buf = file_get_contents("$capi_host/_do_api.php?req=getnext&dte=" . $tim . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));

	return($ary);
}

function confessor_get_current($tim = 0,$json_flag = 0)
{
	global $capi_host;
	global $db;

fwrite(confd,__LINE__ . ": in get_current tim=$tim - json_flag=$json_flag\n");

	if(empty($tim))
		$tim = time();
	$buf = file_get_contents("$capi_host/_do_api.php?req=getcurrent&dte=" . $tim . ($json_flag ? "&json=1" : ''));
fwrite(confd,__LINE__ . ": buf=$buf\n");
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));

fwrite(confd,__LINE__ . ": ary=" . print_r($ary,true) . "\n");
	return($ary);
}

/*====================================================*
 * get_day(<day number (0-6))
 *
 * returns array of all shows for that day in order of start time:
 *
 * array => show (0-n) => 
 *	[sh_id] => 71
 *	[sh_altid] => nightsounds
 *  [sh_memsysid] => xxy
 *	[sh_name] => Pat &amp; Rosie&#039;s Night Sounds
 *	[sh_desc] =>
 *	[sh_url] => 
 *  [sh_facebook] => http://pacificaradioarchives.org
 *  [sh_twitter] => http://pacificaradioarchives.org
 *  [sh_tumblr] => http://pacificaradioarchives.org
 *	[sh_photo] => http://confessor.kpft.org/pix/pat__rosies_night_sounds_71.jpg
 *	[sh_djname] => Pat &amp; Rosie
 *	[sh_email] =>
 *	[start] => 1:00 AM
 *	[end] => 4:00 AM
 *	[sh_shour] => 3600
 *	[sh_len] => 10800
 *	[sh_info] => 64
 *	[day] => Saturday
 *
 *
 *====================================================*/
function confessor_get_day($day_num = -1,$json_flag = 0)
{
	global $capi_host;

	if($day_num < 0)
		$day_num = date("w");
	$buf = file_get_contents("$capi_host/_do_api.php?req=getday&day=$day_num" . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}

function confessor_get_now_playing($tim = 0,$json_flag = 0)
{
	global $capi_host;

	$buf = file_get_contents("$capi_host/_do_api.php?req=getnow" . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}

/*====================================================*
 * archive_get_filnam(<idkey>,<num (optional - defaults to 5)>
 * retrieves array of archive entries:
 * 
 *
 * array(
 * 	[0-n]
 *	(
 *	 	[pubfile] => Array
 *		(
 *			[0] => Array
 *			(
 *				[pf_host] => Sarah Gish
 *				[pf_gname] => Dr. Monica Roberson, a nontraditional women&#39;s wellness doctor
 *				[pf_gtopic] => Only Connect: People and Places That Make Houston Great
 *				[pf_gurl] => 
 *				[pf_issue1] => Health and Medicine
 *				[pf_issue2] => Miscellaneous
 *				[pf_issue3] => 
 *				[pf_notes] => 
 *			)
 *		)
 *		[idkey] => ojcs
 *		[title] => Open Journal - Community Spotlight
 *		[days] => 60
 *		[category] => Public Affairs
 *		[producer] => Community Members
 *		[link] => 
 *		[mp3] => http://archive.kpft.org/mp3/kpft_121219_093000ojcs.mp3
 *		[day] => Wednesday
 *		[date] => December 19, 2012
 *		[def_time] => 1355931000
 *		[expires] => 1361115000
 *		[txt] => 
 *		[lsecs] => 1811
 *	)
 *	.
 *	.
 *	.
 *	[n]
 *	(
 *		etc
 *	)
 *)
 *====================================================*/
function archive_get_filnam($idkey,$num = 0,$json_flag = 0)
{
	global $archive_host;

	$buf = file_get_contents("$archive_host/_sh_do_api.php?req=$idkey&num=$num" . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}

function confessor_get_shows_by_alfa($json_flag = 0)
{
	global $capi_host;

	$buf = file_get_contents("$capi_host/_do_api.php?req=getalfa" . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}

function confessor_get_show_by_key($key,$json_flag = 0)
{
	global $capi_host;

	$buf = file_get_contents("$capi_host/_do_api.php?req=key&key=" . $key . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}

function confessor_get_list($json_flag = 0)
{
	global $capi_host;

	$buf = file_get_contents("$capi_host/_do_api.php?req=list" . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}

function confessor_get_altids($json_flag = 0)
{
	global $capi_host;

	$buf = file_get_contents("$capi_host/_do_api.php?req=altids" . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	$ary = unserialize(base64_decode($buf));
	return($ary);
}

function confessor_get_show_ary($json_flag = 0)
{
	global $capi_host;

	$buf = file_get_contents("$capi_host/_do_api.php?req=getary" . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}

function confessor_get_memsys_ary($json_flag = 0)
{
	global $capi_host;

	$buf = file_get_contents("$capi_host/_do_api.php?req=memsys" . ($json_flag ? "&json=1" : ''));
	$ary = unserialize(base64_decode($buf));
	return($ary);
}

function confessor_get_show_times($key,$json_flag = 0)
{
	global $capi_host;

	$buf = file_get_contents("$capi_host/_do_api.php?req=shotimes&key=$key" . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}

function confessor_get_playlist_by_phid($id,$json_flag = 0)
{
	global $capi_host;

	$buf = file_get_contents("$capi_host/_do_api.php?req=playlist&phid=$id" . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}
function confessor_get_playlist_by_date($dt,$json_flag = 0)
{
	global $capi_host;

	$buf = file_get_contents("$capi_host/_do_api.php?req=playlist&date=" . urlencode($dt) . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}
function confessor_get_playlist_by_id($id,$dtstr,$json_flag = 0)
{
	global $capi_host;

	$buf = file_get_contents("$capi_host/_do_api.php?req=playlist&dt=" . urlencode($dtstr) . "&key=$id" . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}
function confessor_get_all_playlists_by_id($key,$json_flag = 0)
{
	global $capi_host;

	$buf = file_get_contents("$capi_host/_do_api.php?req=all&key=$key" . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}
function confessor_get_pledge($json_flag = 0)
{
	global $capi_host;

	$buf = file_get_contents("$capi_host/_do_api.php?req=pledge" . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}
function confessor_most_recent_playlist($altid,$json_flag = 0)
{
	global $capi_host;

	$buf = file_get_contents("$capi_host/_do_api.php?req=mostrecent&altid=$altid" . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}

function confessor_get_sched($stdte,$endte,$json_flag = 0)
{
	global $capi_host;

	$buf = file_get_contents("$capi_host/_do_api.php?req=sched&stdte=$stdte&endte=$endte". ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}

function confessor_get_nowplaying($json_flag = 0)
{
	global $capi_host;

	$buf = file_get_contents("$capi_host/_do_api.php?req=nowshow" . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}

function confessor_get_nowplaying_short($json_flag = 0)
{
	global $capi_host;

	$buf = file_get_contents("$capi_host/_do_api.php?req=nowshort" . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}

function confessor_get_nowary($json_flag = 0)
{
	global $capi_host;

	$buf = file_get_contents("$capi_host/_do_api.php?req=nowary" . ($json_flag ? "&json=1" : ''));
	if($json_flag)
		$ary = $buf;
	else
		$ary = unserialize(base64_decode($buf));
	return($ary);
}
?>
