from spotipy.oauth2 import SpotifyClientCredentials
from plexapi.server import PlexServer
from plexapi.audio import Track
from typing import List
import requests
import logging
import spotipy
import time
import re
import os


def filterPlexArray(plexItems=[], song="", artists="") -> List[Track]:
    for item in list(plexItems):
        if type(item) != Track:
            plexItems.remove(item)
            continue
        if item.title.lower() != song.lower():
            plexItems.remove(item)
            continue

        # Plex artist metadata is complicated. For "Various Artists" compilations, such as soundtracks,
        # the artist name of the individual track is stored in item.originalTitle. item.artist() in this scenario
        # will return "Various Artists", which is the incorrect artist name.

        # TODO - Might need additional filter that splits plexItem artist containing multiple artists into an array as failover
        # if original attempt does not give results.
        # Example: artistItem.title: "Bad Computer & Nandya Saityr", but artists is an array: ["Bad Computer", "Nandya Saityr"]
        # Split artistItem.title into an array based on if [" and ", " & ", ", "] IF original attempt to find artistItem.title in artists array fails.
        artistItem = item.artist()
        if item.originalTitle != None:
            if not any(
                artist["name"].lower() == item.originalTitle.lower()
                for artist in artists
            ):
                plexItems.remove(item)
                continue
        elif not any(
            artist["name"].lower() == artistItem.title.lower() for artist in artists
        ):
            plexItems.remove(item)
            continue

    return plexItems


def getSpotifyPlaylist(sp: spotipy.client, userId: str, playlistId: str) -> List[str]:
    playlist = sp.user_playlist(userId, playlistId)
    return playlist


# Returns a list of spotify playlist objects
def getSpotifyUserPlaylists(sp: spotipy.client, userId: str) -> List[str]:
    playlists = sp.user_playlists(userId)
    spotifyPlaylists = []
    while playlists:
        playlistItems = playlists["items"]
        for i, playlist in enumerate(playlistItems):
            if playlist["owner"]["id"] == userId:
                spotifyPlaylists.append(getSpotifyPlaylist(sp, userId, playlist["id"]))
        if playlists["next"]:
            playlists = sp.next(playlists)
        else:
            playlists = None
    return spotifyPlaylists


def getSpotifyTracks(sp: spotipy.client, playlist: List[str]) -> List[str]:
    spotifyTracks = []
    tracks = playlist["tracks"]
    spotifyTracks.extend(tracks["items"])
    while tracks["next"]:
        tracks = sp.next(tracks)
        spotifyTracks.extend(tracks["items"])
    return spotifyTracks


def getPlexTracks(plex: PlexServer, spotifyTracks: List[str]) -> List[Track]:
    plexTracks = []
    for spotifyTrack in spotifyTracks:
        track = spotifyTrack["track"]
        logging.info(
            "Searching Plex for: %s by %s"
            % (track["name"], track["artists"][0]["name"])
        )

        # Look for an exact match first.
        try:
            musicTracks = plex.search(track["name"], mediatype="track")
        except:
            logging.info("Issue making plex request")
            continue

        # Try some fuzzy logic.
        cleanedTrackName = ""
        if len(musicTracks) < 1:
            logging.info(
                "Unable to find exact match. Attempting to remove unnecessary strings.."
            )
            if "- Remastered" in track["name"]:
                cleanedTrackName = track["name"].split(" - Remastered")[0]
            if "- Original Mix" in track["name"]:
                cleanedTrackName = track["name"].split(" - Original Mix")[0]
            if "- Extended Mix" in track["name"]:
                cleanedTrackName = track["name"].split(" - Extended Mix")[0]
            try:
                musicTracks = plex.search(cleanedTrackName, mediatype="track")
            except:
                try:
                    musicTracks = plex.search(cleanedTrackName, mediatype="track")
                except:
                    logging.info("Issue making plex request")
                    continue

        if cleanedTrackName != "":
            track["name"] = cleanedTrackName

        if len(musicTracks) > 0:
            plexMusic = filterPlexArray(musicTracks, track["name"], track["artists"])
            if len(plexMusic) > 0:
                logging.info(
                    "Found Plex Song: %s by %s"
                    % (track["name"], track["artists"][0]["name"])
                )
                plexTracks.append(plexMusic[0])
            else:
                with open("missing_tracks.csv", "a") as f:
                    artistName = track["artists"][0]["name"].replace("α", "alpha")
                    artistName = track["artists"][0]["name"].replace("\u03b1", "alpha")
                    artistName = track["artists"][0]["name"].replace("✝✝✝", "crosses")
                    f.write(track["name"] + ", " + artistName + "\n")
                    f.close()

    return plexTracks


def createPlaylist(plex: PlexServer, sp: spotipy.Spotify, playlist: List[str]):
    playlistName = playlist["owner"]["display_name"] + " - " + playlist["name"]
    logging.info("Starting playlist %s" % playlistName)
    plexTracks = getPlexTracks(plex, getSpotifyTracks(sp, playlist))
    if len(plexTracks) > 0:
        try:
            plexPlaylist = plex.playlist(playlistName)
            logging.info("Updating playlist %s" % playlistName)
            plexPlaylist.addItems(plexTracks)
        except:
            logging.info("Creating playlist %s" % playlistName)
            plex.createPlaylist(playlistName, plexTracks)


def parseSpotifyURI(uriString: str) -> dict[str, str]:
    spotifyUriStrings = re.sub(r"^spotify:", "", uriString).split(":")
    spotifyUriParts = {}
    for i, string in enumerate(spotifyUriStrings):
        if i % 2 == 0:
            spotifyUriParts[spotifyUriStrings[i]] = spotifyUriStrings[i + 1]

    return spotifyUriParts


def runSync(plex: PlexServer, sp: spotipy.Spotify, spotifyURIs: List[str]):
    logging.info("Starting a Sync Operation")
    playlists = []

    for spotifyUriParts in spotifyURIs:
        if (
            "user" in spotifyUriParts.keys()
            and "playlist" not in spotifyUriParts.keys()
        ):
            logging.info("Getting playlists for %s" % spotifyUriParts["user"])
            playlists.extend(getSpotifyUserPlaylists(sp, spotifyUriParts["user"]))
        elif "user" in spotifyUriParts.keys() and "playlist" in spotifyUriParts.keys():
            logging.info(
                "Getting playlist %s from user %s"
                % (spotifyUriParts["user"], spotifyUriParts["playlist"])
            )
            playlists.append(
                getSpotifyPlaylist(
                    sp, spotifyUriParts["user"], spotifyUriParts["playlist"]
                )
            )

    for playlist in playlists:
        createPlaylist(plex, sp, playlist)
    logging.info("Finished a Sync Operation")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    spotifyUris = os.environ.get("SPOTIFY_URIS")

    if spotifyUris is None:
        logging.error("No spotify uris")

    baseurl = os.environ.get("PLEX_URL")
    token = os.environ.get("PLEX_TOKEN")

    session = requests.Session()
    session.verify = False
    plex = PlexServer(baseurl, token, session)

    spotifyClientCredentialsManager = SpotifyClientCredentials()
    sp = spotipy.Spotify(client_credentials_manager=spotifyClientCredentialsManager)

    spotifyUris = spotifyUris.split(",")

    spotifyMainUris = []

    for spotifyUri in spotifyUris:
        spotifyUriParts = parseSpotifyURI(spotifyUri)
        spotifyMainUris.append(spotifyUriParts)

    runSync(plex, sp, spotifyMainUris)
