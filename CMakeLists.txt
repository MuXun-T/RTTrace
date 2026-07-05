cmake_minimum_required(VERSION 3.16)

project(trace_mvp LANGUAGES CXX)

enable_testing()

add_subdirectory(collector)
add_subdirectory(tests/cpp)
