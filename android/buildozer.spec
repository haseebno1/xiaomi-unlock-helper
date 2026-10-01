[app]
title = Xiaomi Quota Helper
package.name = xiaomiquota
package.domain = io.github.haseebno1
source.dir = .
source.include_exts = py
version = 1.0.0
requirements = python3==3.11.5,hostpython3==3.11.5,kivy==2.3.0,kivymd==1.2.0,urllib3,ntplib,pyjnius,android
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,ACCESS_NETWORK_STATE,WAKE_LOCK
android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True
p4a.branch = v2024.01.21

[buildozer]
log_level = 2
warn_on_root = 1
