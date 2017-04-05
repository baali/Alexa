import email
import json
import logging
import platform
import signal
import subprocess
import types
from threading import Event

import requests
from monotonic import monotonic
from respeaker import Microphone

from creds import Client_ID, Client_Secret, refresh_token
from respeaker.vad import vad
import time

import contextlib
import wave
from gpiozero import LED, Button
from signal import pause

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__file__)

if platform.machine() == 'mips':
    mp3_player = 'madplay -o wave:- - | aplay -M'
else:
    # mp3_player = 'ffplay -autoexit -nodisp -loglevel quiet -'
    mp3_player = 'mplayer -'

class Alexa:
    """
    Provide Alexa Voice Service based on API v1
    """

    def __init__(self, mic=None):
        self.access_token = None
        self.expire_time = None
        self.session = requests.Session()
        self.mic = mic

    def get_token(self):
        if self.expire_time is None or monotonic() > self.expire_time:
            # get an access token using OAuth
            credential_url = "https://api.amazon.com/auth/o2/token"
            data = {
                "client_id": Client_ID,
                "client_secret": Client_Secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
            start_time = monotonic()
            r = self.session.post(credential_url, data=data)

            if r.status_code != 200:
                raise Exception("Failed to get token. HTTP status code {}".format(r.status_code))

            credentials = r.json()
            self.access_token = credentials["access_token"]
            self.expire_time = start_time + float(credentials["expires_in"])

        return self.access_token

    @staticmethod
    def generate(audio, boundary):
        """
        Generate a iterator for chunked transfer-encoding request of Alexa Voice Service
        Args:
            audio: raw 16 bit LSB audio data
            boundary: boundary of multipart content

        Returns:

        """
        start_time = time.time()
        logger.debug('Start sending speech to Alexa Voice Service')
        chunk = '--%s\r\n' % boundary
        chunk += (
            'Content-Disposition: form-data; name="request"\r\n'
            'Content-Type: application/json; charset=UTF-8\r\n\r\n'
        )

        d = {
            "messageHeader": {
                "deviceContext": [{
                    "name": "playbackState",
                    "namespace": "AudioPlayer",
                    "payload": {
                        "streamId": "",
                        "offsetInMilliseconds": "0",
                        "playerActivity": "IDLE"
                    }
                }]
            },
            "messageBody": {
                "profile": "alexa-close-talk",
                "locale": "en-us",
                "format": "audio/L16; rate=16000; channels=1"
            }
        }

        yield (chunk + json.dumps(d) + '\r\n').encode()

        chunk = '--%s\r\n' % boundary
        chunk += (
            'Content-Disposition: form-data; name="audio"\r\n'
            'Content-Type: audio/L16; rate=16000; channels=1\r\n\r\n'
        )

        yield chunk.encode()

        with contextlib.closing(wave.open('recording.wav', 'wb')) as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            for a in audio:
                wf.writeframes(a)
                yield a

        yield ('--%s--\r\n' % boundary).encode()
        print("Finished sending data to AVS")
        print("Time spent recording audio: " +  str(time.time() - start_time))
        logger.debug('Finished sending speech to Alexa Voice Service')

    @staticmethod
    def pack(audio, boundary):
        print('Start sending speech to Alexa Voice Service')
        logger.debug('Start sending speech to Alexa Voice Service')
        body = '--%s\r\n' % boundary
        body += (
            'Content-Disposition: form-data; name="request"\r\n'
            'Content-Type: application/json; charset=UTF-8\r\n\r\n'
        )

        d = {
            "messageHeader": {
                "deviceContext": [{
                    "name": "playbackState",
                    "namespace": "AudioPlayer",
                    "payload": {
                        "streamId": "",
                        "offsetInMilliseconds": "0",
                        "playerActivity": "IDLE"
                    }
                }]
            },
            "messageBody": {
                "profile": "alexa-close-talk",
                "locale": "en-us",
                "format": "audio/L16; rate=16000; channels=1"
            }
        }

        body += json.dumps(d) + '\r\n'

        body += '--%s\r\n' % boundary
        body += (
            'Content-Disposition: form-data; name="audio"\r\n'
            'Content-Type: audio/L16; rate=16000; channels=1\r\n\r\n'
        )

        body += audio

        body += '--%s--\r\n' % boundary

        return body

    def recognize(self):
        print('Button pressed!')
        audio = self.mic.listen(duration=3)
        url = 'https://access-alexa-na.amazon.com/v1/avs/speechrecognizer/recognize'
        boundary = 'this-is-a-boundary'
        if isinstance(audio, types.GeneratorType):
            headers = {
                'Authorization': 'Bearer %s' % self.get_token(),
                'Content-Type': 'multipart/form-data; boundary=%s' % boundary,
                'Transfer-Encoding': 'chunked',
            }
            data = self.generate(audio, boundary)
        else:
            headers = {
                'Authorization': 'Bearer %s' % self.get_token(),
                'Content-Type': 'multipart/form-data; boundary=%s' % boundary,
            }
            data = self.pack(audio, boundary)

        start_time = time.time()
        r = self.session.post(url, headers=headers, data=data, timeout=20)
        response_waiting_time = time.time() - start_time
        print("Time Alexa request took: " +  str(response_waiting_time))
        self.process_response(r)

    def process_response(self, response):
        logger.debug("Processing Request Response...")
        print("Processing Request Response...")

        if response.status_code == 200:
            data = b"Content-Type: " + response.headers['content-type'].encode('utf-8') + b'\r\n\r\n' + response.content
            msg = email.message_from_bytes(data)
            for payload in msg.get_payload():
                if payload.get_content_type() == "application/json":
                    j = json.loads(payload.get_payload())
                    logger.debug("JSON String Returned: %s", json.dumps(j, indent=2))
                elif payload.get_content_type() == "audio/mpeg":
                    logger.debug('Play ' + payload.get('Content-ID').strip("<>"))

                    f = open(payload.get('Content-ID').strip("<>")+'.wav', 'wb')
                    f.write(payload.get_payload(decode=True))
                    f.close()
                    p = subprocess.Popen(mp3_player, stdin=subprocess.PIPE, shell=True)
                    p.stdin.write(payload.get_payload(decode=True))
                    p.stdin.close()
                    p.wait()
                else:
                    logger.debug("NEW CONTENT TYPE RETURNED: %s", payload.get_content_type())

            # Now process the response
            if 'directives' in j['messageBody']:
                if len(j['messageBody']['directives']) == 0:
                    logger.debug("0 Directives received")

                for directive in j['messageBody']['directives']:
                    if directive['namespace'] == 'SpeechSynthesizer':
                        if directive['name'] == 'speak':
                            print("SpeechSynthesizer audio: " + directive['payload']['audioContent'].lstrip('cid:'))
                            logger.debug(
                                "SpeechSynthesizer audio: " + directive['payload']['audioContent'].lstrip('cid:'))
                    elif directive['namespace'] == 'SpeechRecognizer':
                        if directive['name'] == 'listen':
                            timeout_ms = directive['payload']['timeoutIntervalInMillis']
                            logger.debug("Speech Expected, timeout in: %sms", timeout_ms)

                            self.recognize(self.mic.listen(timeout=timeout_ms / 1000))

                    elif directive['namespace'] == 'AudioPlayer':
                        if directive['name'] == 'play':
                            for stream in directive['payload']['audioItem']['streams']:
                                logger.debug('AudioPlayer audio:' + stream['streamUrl'].lstrip('cid:'))

                    elif directive['namespace'] == "Speaker":
                        # speaker control such as volume
                        if directive['name'] == 'SetVolume':
                            vol_token = directive['payload']['volume']
                            type_token = directive['payload']['adjustmentType']
                            if type_token == 'relative':
                                logger.debug('relative volume adjust')

                            logger.debug("new volume = %s", vol_token)

            # Additional Audio Iten
            elif 'audioItem' in j['messageBody']:
                pass
        elif response.status_code == 204:
            logger.debug("Request Response is null (This is OKAY!)")
        else:
            logger.info("(process_response Error) Status Code: %s", response.status_code)
            response.connection.close()


def main():
    quit_event = Event()
    mic = Microphone(quit_event=quit_event)
    alexa = Alexa(mic)

    def on_quit(signum, frame):
        quit_event.set()

    signal.signal(signal.SIGINT, on_quit)
    right_button = Button(3)
    right_button.when_pressed = alexa.recognize
    pause()
    mic.close()
    logging.debug('Mission completed')

if __name__ == '__main__':
    main()
