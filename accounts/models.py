from operator import itemgetter

from django.conf import settings
from django.db import transaction
from django.contrib.auth.models import User
from django.db import models
from django.db.models import signals, Q
from django.db.models.functions import Lower
from django.contrib.contenttypes.models import ContentType
from django.dispatch import receiver
from django.urls import reverse

from django_thumbs.db.models import ImageWithThumbsField

from registration import models as regmodels

from sanitizer.models import SanitizedTextField

from editor.models import NewQuestion, EditorTag, Project, TimelineItem, SiteBroadcast, EditorItem

class RegistrationManager(regmodels.RegistrationManager):
    @transaction.atomic
    def create_inactive_user(self, username, first_name, last_name, email, password,
                             site, send_email=True):
        """
        Create a new, inactive ``User``, generate a
        ``RegistrationProfile`` and email its activation key to the
        ``User``, returning the new ``User``.

        By default, an activation email will be sent to the new
        user. To disable this, pass ``send_email=False``.
        
        """
        new_user = User.objects.create_user(username, email, password, first_name=first_name, last_name=last_name)
        new_user.is_active = False
        new_user.save()

        registration_profile = self.create_profile(new_user)

        if send_email:
            registration_profile.send_activation_email(site)

        return new_user

class RegistrationProfile(regmodels.RegistrationProfile):
    objects = RegistrationManager()


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    language = models.CharField(max_length=100, default='en-GB')
    bio = SanitizedTextField(default='', allowed_tags=settings.SANITIZER_ALLOWED_TAGS, allowed_attributes=settings.SANITIZER_ALLOWED_ATTRIBUTES)
    question_basket = models.ManyToManyField(NewQuestion, blank=True, related_name='baskets', through='BasketQuestion')
    personal_project = models.ForeignKey(Project, null=True, on_delete=models.SET_NULL,related_name='personal_project_of')
    avatar = ImageWithThumbsField(upload_to='avatars', sizes=((20, 20), (40, 40), (150, 150)), blank=True, null=True, max_length=255, verbose_name='Profile image')
    wrap_lines = models.BooleanField(default=False,verbose_name='Wrap long lines in the code editor?')
    mathjax_url = models.CharField(max_length=300,default='',blank=True,verbose_name='Preferred URL to load MathJax from')

    def sorted_tags(self):
        qs = self.user.own_questions
        tags = EditorTag.objects.filter(question__author=self.user).distinct()
        tag_counts = [(tag, len(qs.filter(tags__id=tag.id))) for tag in tags]
        tag_counts.sort(key=itemgetter(1), reverse=True)

        return tag_counts

    @property
    def recent_questions(self):
        return NewQuestion.objects.filter(editoritem__author=self.user).order_by('-editoritem__last_modified')[:10]

    def projects(self):
        return (Project.objects.filter(owner=self.user) | Project.objects.filter(projectaccess__user=self.user)).distinct().order_by(Lower('name'))

    def all_timeline(self):
        projects = self.user.own_projects.all() | Project.objects.filter(projectaccess__in=self.user.project_memberships.all()) | Project.objects.filter(watching_non_members=self.user)
        nonsticky_broadcasts = SiteBroadcast.objects.visible_now().exclude(sticky=True)
        nonsticky_broadcast_timelineitems = TimelineItem.objects.filter(object_content_type=ContentType.objects.get_for_model(SiteBroadcast), object_id__in=nonsticky_broadcasts)

        items = TimelineItem.objects.filter(
            Q(editoritems__in=self.user.watched_items.all()) | 
            Q(editoritems__project__in=projects) |
            Q(projects__in=projects)
        )

        items = (items | nonsticky_broadcast_timelineitems).order_by('-date')

        return items

    def public_timeline(self):
        return self.user.timelineitems.order_by('-date')

    def get_absolute_url(self):
        return reverse('view_profile', args=(self.pk,))

class EditorItemViewed(models.Model):
    userprofile = models.ForeignKey(UserProfile, related_name='last_viewed_items', on_delete=models.CASCADE)
    item = models.ForeignKey(EditorItem,related_name='views', on_delete=models.CASCADE)
    date = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-date',)

@receiver(signals.post_save, sender=EditorItemViewed)
def truncate_last_viewed_items(instance, created, **kwargs):
    views = EditorItemViewed.objects.filter(userprofile=instance.userprofile)
    old = views[5:].values_list('id',flat=True)
    if old:
        views.filter(pk__in=old).delete()

class BasketQuestion(models.Model):
    class Meta:
        ordering = ['qn_order']
        unique_together = ('profile', 'question')
        
    profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE)
    question = models.ForeignKey(NewQuestion, on_delete=models.CASCADE)
    qn_order = models.PositiveIntegerField()


@receiver(signals.post_save, sender=User)
def createUserProfile(instance, created, **kwargs):
    """
        Create a UserProfile and personal workspace
    """
    workspace_name = "{}'s workspace".format(instance.first_name)
    if created:
        profile = UserProfile.objects.create(user=instance)
        profile.personal_project = Project.objects.create(name=workspace_name, owner=instance)
        profile.save()
    else:
        if instance.userprofile and instance.userprofile.personal_project:
            instance.userprofile.personal_project.name = workspace_name
            instance.userprofile.personal_project.save()
