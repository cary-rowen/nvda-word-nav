# -*- coding: UTF-8 -*-
#A part of  WordNav addon for NVDA
#Copyright (C) 2020 Tony Malykh
#This file is covered by the GNU General Public License.
#See the file COPYING.txt for more details.

import addonHandler
import api
import bisect
import collections
import config
import controlTypes
import core
import copy
import ctypes
from ctypes import create_string_buffer, byref
import documentBase
import editableText
import globalPluginHandler
import gui
from gui import guiHelper, nvdaControls
from gui.settingsDialogs import SettingsPanel
import inputCore
import itertools
import json
import keyboardHandler
from logHandler import log
import NVDAHelper
from NVDAObjects import behaviors
from NVDAObjects.window import winword
import nvwave
import operator
import os
import re
import sayAllHandler
from scriptHandler import script, willSayAllResume
import speech
import struct
import textInfos
import threading
import time
import tones
import types
import ui
import watchdog
import wave
import winUser
import wx

winmm = ctypes.windll.winmm


debug = False
if debug:
    import threading
    LOG_FILE_NAME = "C:\\Users\\tony\\Dropbox\\1.txt"
    f = open(LOG_FILE_NAME, "w")
    f.close()
    LOG_MUTEX = threading.Lock()
    def mylog(s):
        with LOG_MUTEX:
            f = open(LOG_FILE_NAME, "a", encoding='utf-8')
            print(s, file=f)
            #f.write(s.encode('UTF-8'))
            #f.write('\n')
            f.close()
else:
    def mylog(*arg, **kwarg):
        pass

def myAssert(condition):
    if not condition:
        raise RuntimeError("Assertion failed")

module = "wordNav"
def initConfiguration():
    confspec = {
        "overrideMoveByWord" : "boolean( default=True)",
        "enableMoveByWordEx" : "boolean( default=True)",
        "assignmentIndex": "integer( default=9, min=0, max=10)",
        "bulkyWordPunctuation" : f"string( default='():')",
        "applicationsBlacklist" : f"string( default='')",
    }
    config.conf.spec[module] = confspec

def getConfig(key):
    value = config.conf[module][key]
    return value
def setConfig(key, value):
    config.conf[module][key] = value


addonHandler.initTranslation()
initConfiguration()


# Regular expression for the beginning of a word. Matches:
#  1. End of string
# 2. Beginning of any word: \b\w
# 3. Punctuation mark preceded by non-punctuation mark: (?<=[\w\s])[^\w\s]
# 4. Punctuation mark preceded by beginning of the string
wordReString = r'$|\b\w|(?<=[\w\s])[^\w\s]|^[^\w\s]'
wordRe = re.compile(wordReString)

# Regular expression for beginning of fine word. This word definition breaks
# camelCaseIdentifiers  and underscore_separated_identifiers into separate sections for easier editing.
# Includes all conditions for wordRe plus additionally:
# 5. Word letter, that is not  underscore, preceded by underscore.
#6. Capital letter preceded by a lower-case letter.
wordReFineString = wordReString + "|(?<=_)(?!_)\w|(?<=[a-z])[A-Z]"
wordReFine = re.compile(wordReFineString)

# Regular expression for bulky words. Treats any punctuation signs as part of word.
# Matches either:
# 1. End of string, or
# 2.     Non-space character preceded either by beginning of the string or a space character.
regexpEscapeSet = set(r". \ + * ? [ ^ ] $ ( ) { } = ! < > | : -".split())
def escapeRegex(s):
    def escapeCharacter(c):
        if c in regexpEscapeSet:
            return f"\\{c}"
        return c
    return "".join(map(escapeCharacter, s))
def generateWordRebulky(punctuation=None):
    if punctuation is None:
        punctuation = getConfig("bulkyWordPunctuation")
    punctuation = escapeRegex(punctuation)
    space = f"\\s{punctuation}"
    wordReBulkyString = f"$|(^|(?<=[{space}]))[^{space}]"
    wordReBulky = re.compile(wordReBulkyString)
    return wordReBulky

# These constants map command assignment combo box index to actual functions
# w stands for navigate by word
# b and f stand for navigate by bulky/fine word
# 0 stands for unassigned
leftControlFunctions = "wwwwwbfwwbf"
rightControlFunctions = "wwwbfwwbfww"
controlWindowsFunctions = "0bf0000fbfb"

def getRegexByFunction(functions, index=None):
    if index is None:
        index = getConfig("assignmentIndex")
    function = functions[index]
    if function == "w":
        return wordRe
    elif function == "b":
        return generateWordRebulky()
    elif function == "f":
        return wordReFine
    else:
        return None

class SettingsDialog(SettingsPanel):
    # Translators: Title for the settings dialog
    title = _("WordNav")
    commandAssignmentText = [
        "Left or Right Control = navigate by word, Control+Windows = unassigned",
        "Left or Right Control = navigate by word, Control+Windows = navigate by bulky word",
        "Left or Right Control = navigate by word, Control+Windows = navigate by fine word",
        "Left Control = navigate by word, Right Control = navigate by bulky word, Control+Windows = unassigned",
        "Left Control = navigate by word, Right Control = navigate by fine word, Control+Windows = unassigned",
        "Left Control = navigate by bulky word, Right Control = navigate by word, Control+Windows = unassigned",
        "Left Control = navigate by fine word, Right Control = navigate by word, Control+Windows = unassigned",
        "Left Control = navigate by word, Right Control = navigate by bulky word, Control+Windows = navigate by fine word",
        "Left Control = navigate by word, Right Control = navigate by fine word, Control+Windows = navigate by bulky word",
        "Left Control = navigate by bulky word, Right Control = navigate by word, Control+Windows = navigate by fine word",
        "Left Control = navigate by fine word, Right Control = navigate by word, Control+Windows = navigate by bulky word",
    ]

    def makeSettings(self, settingsSizer):
        sHelper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
      # checkbox override move by word
        # Translators: Checkbox for override move by word
        label = _("Use enhanced move by word commands for control+LeftArrow/RightArrow in editables.")
        self.overrideMoveByWordCheckbox = sHelper.addItem(wx.CheckBox(self, label=label))
        self.overrideMoveByWordCheckbox.Value = getConfig("overrideMoveByWord")
      # checkbox enableMoveByWordEx
        # Translators: Checkbox for enableMoveByWordEx
        label = _("Enable move by fine/bulky word commands with Control+Windows+LeftArrow/RightArrow in editables. Left control moves by fine word, right control moves by bulky word.")
        self.enableMoveByWordExCheckbox = sHelper.addItem(wx.CheckBox(self, label=label))
        self.enableMoveByWordExCheckbox.Value = getConfig("enableMoveByWordEx")
      # Command assignment Combo box
        # Translators: Label for Command assignment combo box
        label = _("WordNav commands assignment:")
        self.commandAssignmentCombobox = sHelper.addLabeledControl(label, wx.Choice, choices=self.commandAssignmentText)
        index = getConfig("assignmentIndex")
        self.commandAssignmentCombobox.Selection = index

      # bulkyWordPunctuation
        # Translators: Label for bulkyWordPunctuation edit box
        self.bulkyWordPunctuationEdit = gui.guiHelper.LabeledControlHelper(self, _("Bulky word separators:"), wx.TextCtrl).control
        self.bulkyWordPunctuationEdit.Value = getConfig("bulkyWordPunctuation")

      # applicationsBlacklist edit
        # Translators: Label for blacklisted applications edit box
        self.applicationsBlacklistEdit = gui.guiHelper.LabeledControlHelper(self, _("Disable SentenceNav in applications (comma-separated list)"), wx.TextCtrl).control
        self.applicationsBlacklistEdit.Value = getConfig("applicationsBlacklist")

    def onSave(self):
        setConfig("overrideMoveByWord", self.overrideMoveByWordCheckbox.Value)
        setConfig("enableMoveByWordEx", self.enableMoveByWordExCheckbox.Value)
        setConfig("assignmentIndex", self.commandAssignmentCombobox.Selection)
        setConfig("bulkyWordPunctuation", self.bulkyWordPunctuationEdit.Value)
        setConfig("applicationsBlacklist", self.applicationsBlacklistEdit.Value)



class Beeper:
    BASE_FREQ = speech.IDT_BASE_FREQUENCY
    def getPitch(self, indent):
        return self.BASE_FREQ*2**(indent/24.0) #24 quarter tones per octave.

    BEEP_LEN = 10 # millis
    PAUSE_LEN = 5 # millis
    MAX_CRACKLE_LEN = 400 # millis
    MAX_BEEP_COUNT = MAX_CRACKLE_LEN // (BEEP_LEN + PAUSE_LEN)

    def __init__(self):
        self.player = nvwave.WavePlayer(
            channels=2,
            samplesPerSec=int(tones.SAMPLE_RATE),
            bitsPerSample=16,
            outputDevice=config.conf["speech"]["outputDevice"],
            wantDucking=False
        )



    def fancyCrackle(self, levels, volume):
        levels = self.uniformSample(levels, self.MAX_BEEP_COUNT )
        beepLen = self.BEEP_LEN
        pauseLen = self.PAUSE_LEN
        pauseBufSize = NVDAHelper.generateBeep(None,self.BASE_FREQ,pauseLen,0, 0)
        beepBufSizes = [NVDAHelper.generateBeep(None,self.getPitch(l), beepLen, volume, volume) for l in levels]
        bufSize = sum(beepBufSizes) + len(levels) * pauseBufSize
        buf = ctypes.create_string_buffer(bufSize)
        bufPtr = 0
        for l in levels:
            bufPtr += NVDAHelper.generateBeep(
                ctypes.cast(ctypes.byref(buf, bufPtr), ctypes.POINTER(ctypes.c_char)),
                self.getPitch(l), beepLen, volume, volume)
            bufPtr += pauseBufSize # add a short pause
        self.player.stop()
        self.player.feed(buf.raw)

    def simpleCrackle(self, n, volume):
        return self.fancyCrackle([0] * n, volume)


    NOTES = "A,B,H,C,C#,D,D#,E,F,F#,G,G#".split(",")
    NOTE_RE = re.compile("[A-H][#]?")
    BASE_FREQ = 220
    def getChordFrequencies(self, chord):
        myAssert(len(self.NOTES) == 12)
        prev = -1
        result = []
        for m in self.NOTE_RE.finditer(chord):
            s = m.group()
            i =self.NOTES.index(s)
            while i < prev:
                i += 12
            result.append(int(self.BASE_FREQ * (2 ** (i / 12.0))))
            prev = i
        return result

    def fancyBeep(self, chord, length, left=10, right=10):
        beepLen = length
        freqs = self.getChordFrequencies(chord)
        intSize = 8 # bytes
        bufSize = max([NVDAHelper.generateBeep(None,freq, beepLen, right, left) for freq in freqs])
        if bufSize % intSize != 0:
            bufSize += intSize
            bufSize -= (bufSize % intSize)
        self.player.stop()
        bbs = []
        result = [0] * (bufSize//intSize)
        for freq in freqs:
            buf = ctypes.create_string_buffer(bufSize)
            NVDAHelper.generateBeep(buf, freq, beepLen, right, left)
            bytes = bytearray(buf)
            unpacked = struct.unpack("<%dQ" % (bufSize // intSize), bytes)
            result = map(operator.add, result, unpacked)
        maxInt = 1 << (8 * intSize)
        result = map(lambda x : x %maxInt, result)
        packed = struct.pack("<%dQ" % (bufSize // intSize), *result)
        self.player.feed(packed)

    def uniformSample(self, a, m):
        n = len(a)
        if n <= m:
            return a
        # Here assume n > m
        result = []
        for i in range(0, m*n, n):
            result.append(a[i  // m])
        return result
    def stop(self):
        self.player.stop()


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    scriptCategory = _("WordNav")

    def __init__(self, *args, **kwargs):
        super(GlobalPlugin, self).__init__(*args, **kwargs)
        self.createMenu()
        self.injectHooks()
        self.beeper = Beeper()

    def createMenu(self):
        gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(SettingsDialog)

    def terminate(self):
        self.removeHooks()
        gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(SettingsDialog)

    def injectHooks(self):
        self.originalMoveByWord = editableText.EditableText.script_caret_moveByWord
        editableText.EditableText.script_caret_moveByWord = lambda selfself, gesture, *args, **kwargs: self.script_caretMoveByWord(selfself, gesture, *args, **kwargs)
        editableText.EditableText.script_caret_moveByWordEx = lambda selfself, gesture, *args, **kwargs: self.script_caretMoveByWordEx(selfself, gesture, *args, **kwargs)
        editableText.EditableText._EditableText__gestures["kb:control+Windows+leftArrow"] = "caret_moveByWordEx",
        editableText.EditableText._EditableText__gestures["kb:control+Windows+RightArrow"] = "caret_moveByWordEx",


    def  removeHooks(self):
        editableText.EditableText.script_caret_moveByWord = self.originalMoveByWord
        del editableText.EditableText.script_caret_moveByWordEx
        del editableText.EditableText._EditableText__gestures["kb:control+Windows+leftArrow"]
        del editableText.EditableText._EditableText__gestures["kb:control+Windows+RightArrow"]


    def script_caretMoveByWord(self, selfself, gesture):
        if not getConfig('overrideMoveByWord'):
            return self.originalMoveByWord(selfself, gesture)
        onError = lambda e: self.originalMoveByWord(selfself, gesture)
        functions = None
        for modVk, modExt in gesture.generalizedModifiers:
            if modVk == winUser.VK_CONTROL:
                if not modExt:
                    # Left control
                    functions = leftControlFunctions
                else:
                    # Right control
                    functions = rightControlFunctions
        if functions is None:
            raise Exception("Control is not pressed - impossible condition!")
        regex = getRegexByFunction(functions)
        return self.caretMoveByWordImpl(gesture, regex, onError)


    def script_caretMoveByWordEx(self, selfself, gesture):
        regex = getRegexByFunction(controlWindowsFunctions)
        if not getConfig('overrideMoveByWord') or regex is None:
            gesture.send()
            return
        def onError(e):
            raise e
        return self.caretMoveByWordImpl(gesture, regex, onError)

    def caretMoveByWordImpl(self, gesture, wordRe, onError):
        try:
            if 'leftArrow' == gesture.mainKeyName:
                direction = -1
            elif 'rightArrow' == gesture.mainKeyName:
                direction = 1
            else:
                return onError(None)
            focus = api.getFocusObject()
            caretInfo = focus.makeTextInfo(textInfos.POSITION_CARET)
            caretInfo.collapse(end=(direction > 0))
            lineInfo = caretInfo.copy()
            lineInfo.expand(textInfos.UNIT_PARAGRAPH)
            offsetInfo = lineInfo.copy()
            offsetInfo.setEndPoint(caretInfo, 'endToEnd')
            caret = len(offsetInfo.text)
            for lineAttempt in range(100):
                lineText = lineInfo.text.rstrip('\r\n')
                isEmptyLine = len(lineText.strip()) == 0
                boundaries = [m.start() for m in wordRe.finditer(lineText)]
                boundaries = sorted(list(set(boundaries)))
                if direction > 0:
                    newWordIndex = bisect.bisect_right(boundaries, caret)
                else:
                    newWordIndex = bisect.bisect_left(boundaries, caret) - 1
                if not isEmptyLine and (0 <= newWordIndex < len(boundaries)):
                    if lineAttempt == 0:
                        adjustment = boundaries[newWordIndex] - caret
                        newInfo = caretInfo
                        newInfo.move(textInfos.UNIT_CHARACTER, adjustment)
                    else:
                        newInfo = lineInfo
                        if direction > 0:
                            adjustment =  boundaries[newWordIndex]
                            newInfo.collapse(end=False)
                        else:
                            adjustment =  boundaries[newWordIndex] - len(lineInfo.text)
                            newInfo.collapse(end=True)
                        result = newInfo.move(textInfos.UNIT_CHARACTER, adjustment)
                    if newWordIndex + 1 < len(boundaries):
                        newInfo.move(
                            textInfos.UNIT_CHARACTER,
                            boundaries[newWordIndex + 1] - boundaries[newWordIndex],
                            endPoint='end',
                        )
                    newInfo.updateCaret()
                    speech.speakTextInfo(newInfo, unit=textInfos.UNIT_WORD, reason=controlTypes.REASON_CARET)
                    return
                else:
                    lineInfo.collapse()
                    result = lineInfo.move(textInfos.UNIT_PARAGRAPH, direction)
                    if result == 0:
                        self.beeper.fancyBeep('HF', 100, left=25, right=25)
                        return
                    lineInfo.expand(textInfos.UNIT_PARAGRAPH)
                    # now try to find next word again on next/previous line
                    if direction > 0:
                        caret = -1
                    else:
                        caret = len(lineInfo.text)
            #raise Exception('Failed to find next word')
            self.beeper.fancyBeep('HF', 100, left=25, right=25)
        except NotImplementedError as e:
            return onError(e)