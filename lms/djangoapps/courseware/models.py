"""
WE'RE USING MIGRATIONS!

If you make changes to this model, be sure to create an appropriate migration
file and check it in at the same time as your model changes. To do that,

1. Go to the mitx dir
2. ./manage.py schemamigration courseware --auto description_of_your_change
3. Add the migration file created in mitx/courseware/migrations/


ASSUMPTIONS: modules have unique IDs, even across different module_types

"""
from datetime import datetime, timedelta
from calendar import timegm

from django.db import models
from django.contrib.auth.models import User

class StudentModule(models.Model):
    """
    Keeps student state for a particular module in a particular course.
    """
    # For a homework problem, contains a JSON
    # object consisting of state
    MODULE_TYPES = (('problem', 'problem'),
                    ('video', 'video'),
                    ('html', 'html'),
                    )
    ## These three are the key for the object
    module_type = models.CharField(max_length=32, choices=MODULE_TYPES, default='problem', db_index=True)

    # Key used to share state. By default, this is the module_id,
    # but for abtests and the like, this can be set to a shared value
    # for many instances of the module.
    # Filename for homeworks, etc.
    module_state_key = models.CharField(max_length=255, db_index=True, db_column='module_id')
    student = models.ForeignKey(User, db_index=True)
    course_id = models.CharField(max_length=255, db_index=True)

    class Meta:
        unique_together = (('student', 'module_state_key', 'course_id'),)

    ## Internal state of the object
    state = models.TextField(null=True, blank=True)

    ## Grade, and are we done?
    grade = models.FloatField(null=True, blank=True, db_index=True)
    max_grade = models.FloatField(null=True, blank=True)
    DONE_TYPES = (('na', 'NOT_APPLICABLE'),
                    ('f', 'FINISHED'),
                    ('i', 'INCOMPLETE'),
                    )
    done = models.CharField(max_length=8, choices=DONE_TYPES, default='na', db_index=True)

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    modified = models.DateTimeField(auto_now=True, db_index=True)

    def __unicode__(self):
        return '/'.join([self.course_id, self.module_type,
                         self.student.username, self.module_state_key, str(self.state)[:20]])


# TODO (cpennington): Remove these once the LMS switches to using XModuleDescriptors


class StudentModuleCache(object):
    """
    A cache of StudentModules for a specific student
    """
    def __init__(self, course_id, user, descriptors, select_for_update=False):
        '''
        Find any StudentModule objects that are needed by any descriptor
        in descriptors. Avoids making multiple queries to the database.
        Note: Only modules that have store_state = True or have shared
        state will have a StudentModule.

        Arguments
        user: The user for which to fetch maching StudentModules
        descriptors: An array of XModuleDescriptors.
        select_for_update: Flag indicating whether the rows should be locked until end of transaction
        '''
        if user.is_authenticated():
            module_ids = self._get_module_state_keys(descriptors)

            # This works around a limitation in sqlite3 on the number of parameters
            # that can be put into a single query
            self.cache = []
            chunk_size = 500
            for id_chunk in [module_ids[i:i + chunk_size] for i in xrange(0, len(module_ids), chunk_size)]:
                if select_for_update:
                    self.cache.extend(StudentModule.objects.select_for_update().filter(
                        course_id=course_id,
                        student=user,
                        module_state_key__in=id_chunk)
                    )
                else:
                    self.cache.extend(StudentModule.objects.filter(
                        course_id=course_id,
                        student=user,
                        module_state_key__in=id_chunk)
                    )

        else:
            self.cache = []

    @classmethod
    def cache_for_descriptor_descendents(cls, course_id, user, descriptor, depth=None,
                                         descriptor_filter=lambda descriptor: True,
                                         select_for_update=False):
        """
        course_id: the course in the context of which we want StudentModules.
        user: the django user for whom to load modules.
        descriptor: An XModuleDescriptor
        depth is the number of levels of descendent modules to load StudentModules for, in addition to
            the supplied descriptor. If depth is None, load all descendent StudentModules
        descriptor_filter is a function that accepts a descriptor and return wether the StudentModule
            should be cached
        select_for_update: Flag indicating whether the rows should be locked until end of transaction
        """

        def get_child_descriptors(descriptor, depth, descriptor_filter):
            if descriptor_filter(descriptor):
                descriptors = [descriptor]
            else:
                descriptors = []

            if depth is None or depth > 0:
                new_depth = depth - 1 if depth is not None else depth

                for child in descriptor.get_children():
                    descriptors.extend(get_child_descriptors(child, new_depth, descriptor_filter))

            return descriptors

        descriptors = get_child_descriptors(descriptor, depth, descriptor_filter)

        return StudentModuleCache(course_id, user, descriptors, select_for_update)

    def _get_module_state_keys(self, descriptors):
        '''
        Get a list of the state_keys needed for StudentModules
        required for this module descriptor

        descriptor_filter is a function that accepts a descriptor and return wether the StudentModule
            should be cached
        '''
        keys = []
        for descriptor in descriptors:
            if descriptor.stores_state:
                keys.append(descriptor.location.url())

            shared_state_key = getattr(descriptor, 'shared_state_key', None)
            if shared_state_key is not None:
                keys.append(shared_state_key)

        return keys

    def lookup(self, course_id, module_type, module_state_key):
        '''
        Look for a student module with the given course_id, type, and id in the cache.

        cache -- list of student modules

        returns first found object, or None
        '''
        for o in self.cache:
            if (o.course_id == course_id and
                o.module_type == module_type and
                o.module_state_key == module_state_key):
                return o
        return None

    def append(self, student_module):
        self.cache.append(student_module)


class OfflineComputedGrade(models.Model):
    """
    Table of grades computed offline for a given user and course.
    """
    user = models.ForeignKey(User, db_index=True)
    course_id = models.CharField(max_length=255, db_index=True)

    created = models.DateTimeField(auto_now_add=True, null=True, db_index=True)
    updated = models.DateTimeField(auto_now=True, db_index=True)

    gradeset = models.TextField(null=True, blank=True)		# grades, stored as JSON

    class Meta:
        unique_together = (('user', 'course_id'), )

    def __unicode__(self):
        return "[OfflineComputedGrade] %s: %s (%s) = %s" % (self.user, self.course_id, self.created, self.gradeset)


class OfflineComputedGradeLog(models.Model):
    """
    Log of when offline grades are computed.
    Use this to be able to show instructor when the last computed grades were done.
    """
    class Meta:
        ordering = ["-created"]
        get_latest_by = "created"

    course_id = models.CharField(max_length=255, db_index=True)
    created = models.DateTimeField(auto_now_add=True, null=True, db_index=True)
    seconds = models.IntegerField(default=0)	# seconds elapsed for computation
    nstudents = models.IntegerField(default=0)

    def __unicode__(self):
        return "[OCGLog] %s: %s" % (self.course_id, self.created)

class TimedModule(models.Model):
    """
    Keeps student state for a timed activity in a particular course.
    Includes information about time accommodations granted,
    time started, and ending time.
    """
    ## These three are the key for the object

    # Key used to share state. By default, this is the module_id,
    # but for abtests and the like, this can be set to a shared value
    # for many instances of the module.
    # Filename for homeworks, etc.
    module_state_key = models.CharField(max_length=255, db_index=True, db_column='module_id')
    student = models.ForeignKey(User, db_index=True)
    course_id = models.CharField(max_length=255, db_index=True)

    class Meta:
        unique_together = (('student', 'module_state_key', 'course_id'),)

    # For a timed activity, we are only interested here
    # in time-related accommodations, and these should be disjoint.
    # (For proctored exams, it is possible to have multiple accommodations
    # apply to an exam, so they require accommodating a multi-choice.)
    TIME_ACCOMMODATION_CODES = (('NONE', 'No Time Accommodation'),
                      ('ADDHALFTIME', 'Extra Time - 1 1/2 Time'),
                      ('ADD30MIN', 'Extra Time - 30 Minutes'),
                      ('DOUBLE', 'Extra Time - Double Time'),
                      ('TESTING', 'Extra Time -- Large amount for testing purposes')
                    )
    accommodation_code = models.CharField(max_length=12, choices=TIME_ACCOMMODATION_CODES, default='NONE', db_index=True)

    def _get_accommodated_duration(self, duration):
        ''' 
        Get duration for activity, as adjusted for accommodations.
        Input and output are expressed in seconds.
        '''
        if self.accommodation_code == 'NONE':
            return duration
        elif self.accommodation_code == 'ADDHALFTIME':
            # TODO:  determine what type to return
            return int(duration * 1.5)
        elif self.accommodation_code == 'ADD30MIN':
            return (duration + (30 * 60))
        elif self.accommodation_code == 'DOUBLE':
            return (duration * 2)
        elif self.accommodation_code == 'TESTING':
            # when testing, set timer to run for a week at a time.
            return 3600 * 24 * 7
       
    # store state:
    
    beginning_at = models.DateTimeField(null=True, db_index=True)
    ending_at = models.DateTimeField(null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    modified_at = models.DateTimeField(auto_now=True, db_index=True)

    @property
    def has_begun(self):
        return self.beginning_at is not None
    
    @property    
    def has_ended(self):
        if not self.ending_at:
            return False
        return self.ending_at < datetime.utcnow()
        
    def begin(self, duration):
        ''' 
        Sets the starting time and ending time for the activity,
        based on the duration provided (in seconds).
        '''
        self.beginning_at = datetime.utcnow()
        modified_duration = self._get_accommodated_duration(duration)
        datetime_duration = timedelta(seconds=modified_duration)
        self.ending_at = self.beginning_at + datetime_duration
        
    def get_end_time_in_ms(self):
        return (timegm(self.ending_at.timetuple()) * 1000)

    def __unicode__(self):
        return '/'.join([self.course_id, self.student.username, self.module_state_key])

